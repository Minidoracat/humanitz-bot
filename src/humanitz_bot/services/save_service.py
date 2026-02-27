"""存檔解析服務 — 管理 uesave 解析、資料提取與查詢。

透過子程序執行 uesave 和 JSON 提取，避免主程序記憶體暴漲。
解析後的資料儲存在 SQLite 中供快速查詢。
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from humanitz_bot.services.database import Database

logger = logging.getLogger("humanitz_bot.services.save_service")

_UESAVE_BIN = "uesave"

# LGSM 標準存檔相對路徑（從 serverfiles 往下）
_SAVE_RELATIVE = "HumanitZServer/Saved/SaveGames/SaveList/Default/Save_DedicatedSaveMP.sav"

# LGSM 標準安裝目錄 glob 模式
_LGSM_GLOB = "/home/*/serverfiles/" + _SAVE_RELATIVE



@dataclass
class SavePlayerData:
    """從存檔提取的玩家摘要資料"""

    steam_id: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    health: float = 0.0
    hunger: float = 0.0
    thirst: float = 0.0
    stamina: float = 0.0
    infection: float = 0.0
    bites: int = 0
    survival_days: int = 0
    profession: str = ""
    is_male: bool = True
    # 擊殺統計
    zombies_killed: int = 0
    headshots: int = 0
    melee_kills: int = 0
    gun_kills: int = 0
    blast_kills: int = 0
    fist_kills: int = 0
    vehicle_kills: int = 0
    takedown_kills: int = 0
    fish_caught: int = 0
    times_bitten: int = 0
    challenges: dict[str, float] | None = None
    # 由 PlayerIdentityService 填入
    player_name: str = ""

@dataclass
class SaveGameState:
    """遊戲狀態摘要"""

    days_passed: int = 0
    season_day: int = 0
    random_seed: int = 0


class SaveService:
    """存檔解析服務。

    負責：
    1. 呼叫 uesave to-json 將 .sav 轉為 JSON（子程序）
    2. 呼叫 save_extractor 從 JSON 提取關鍵資料（子程序，記憶體隔離）
    3. 將提取的資料匯入 SQLite
    4. 提供查詢 API（委派給 Database）
    """

    def __init__(
        self,
        db: Database,
        save_file_path: str = "",
        save_json_path: str = "/tmp/main_save.json",
    ) -> None:
        self._db = db
        self._save_json_path = Path(save_json_path)
        # /tmp/main_save.json → /tmp/main_save_extract.json
        json_stem = self._save_json_path.stem
        self._extract_json_path = self._save_json_path.with_name(json_stem + "_extract.json")
        self._parsing = False
        self._last_parse_time: float = 0.0
        self._uesave_available: bool = False

        # 解析存檔路徑
        self._save_file_path: Path | None = self._resolve_save_path(save_file_path)

        # 檢查 uesave 是否可用
        self._check_uesave()

    def _check_uesave(self) -> None:
        """檢查 uesave CLI 是否已安裝。"""
        uesave_path = shutil.which(_UESAVE_BIN)
        if uesave_path:
            self._uesave_available = True
            logger.info("uesave found: %s", uesave_path)
        else:
            self._uesave_available = False
            logger.warning(
                "uesave CLI not found. Install from https://github.com/trumank/uesave-rs"
            )

    @staticmethod
    def _resolve_save_path(user_path: str) -> Path | None:
        """解析存檔路徑：使用者指定 > LGSM glob 自動偵測。"""
        if user_path:
            p = Path(user_path)
            if p.exists():
                logger.info("Using save file path from config: %s", p)
                return p
            logger.warning("Configured save file not found: %s", user_path)
            return None

        # 自動偵測：搜尋 LGSM 標準安裝路徑 /home/*/serverfiles/...
        matches = sorted(Path("/").glob(_LGSM_GLOB.lstrip("/")))
        if matches:
            logger.info("Auto-detected save file (LGSM): %s", matches[0])
            return matches[0]

        logger.warning(
            "Save file not found. Set SAVE_FILE_PATH in .env. "
            "LGSM example: /home/<user>/serverfiles/%s",
            _SAVE_RELATIVE,
        )
        return None

    @property
    def is_available(self) -> bool:
        """uesave 和存檔路徑是否都可用。"""
        return self._uesave_available and self._save_file_path is not None

    @property
    def is_parsing(self) -> bool:
        """是否正在解析中。"""
        return self._parsing

    def is_stale(self, max_age_seconds: int) -> bool:
        """快取是否已過期。"""
        if self._last_parse_time == 0.0:
            # 嘗試從 DB 載入
            meta = self._db.get_save_meta()
            if meta and meta.get("last_parse_time"):
                try:
                    dt = datetime.fromisoformat(meta["last_parse_time"])
                    self._last_parse_time = dt.timestamp()
                except (ValueError, TypeError):
                    return True
            else:
                return True

        return (time.time() - self._last_parse_time) >= max_age_seconds

    def seconds_since_parse(self) -> float:
        """距離上次解析的秒數。"""
        if self._last_parse_time == 0.0:
            return float("inf")
        return time.time() - self._last_parse_time

    async def parse_save(self) -> bool:
        """執行完整的存檔解析流程。

        Returns:
            True 表示解析成功，False 表示失敗。
        """
        if self._parsing:
            logger.warning("Parse already in progress, skipping")
            return False

        if not self.is_available:
            logger.error("Cannot parse: uesave or save file not available")
            return False

        if self._save_file_path is None:
            logger.error("Save file path is None despite is_available check")
            return False

        self._parsing = True
        start = time.monotonic()

        try:
            # Step 1: uesave to-json
            success = await self._run_uesave()
            if not success:
                return False

            # Step 2: 提取子程序（記憶體隔離）
            success = await self._run_extractor()
            if not success:
                return False

            # Step 3: 匯入 SQLite
            player_count = await asyncio.to_thread(self._import_to_db)

            elapsed = time.monotonic() - start

            # 更新 meta
            save_mtime = datetime.fromtimestamp(
                self._save_file_path.stat().st_mtime
            ).isoformat()

            await asyncio.to_thread(
                self._db.upsert_save_meta,
                last_parse_time=datetime.now().isoformat(),
                parse_duration=elapsed,
                save_file_mtime=save_mtime,
                player_count=player_count,
            )

            self._last_parse_time = time.time()

            logger.info("Save parse completed in %.1fs", elapsed)
            return True

        except Exception:
            logger.exception("Save parse failed")
            return False
        finally:
            self._parsing = False

    async def _run_uesave(self) -> bool:
        """執行 uesave to-json 子程序。"""
        if self._save_file_path is None:
            logger.error("Save file path is None")
            return False

        logger.info("Running uesave to-json: %s", self._save_file_path)

        try:
            proc = await asyncio.create_subprocess_exec(
                _UESAVE_BIN,
                "to-json",
                "--input",
                str(self._save_file_path),
                "--output",
                str(self._save_json_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logger.error("Subprocess timed out after 120s: uesave to-json")
                return False

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.error("uesave failed (rc=%d): %s", proc.returncode, err_msg)
                return False

            if stderr:
                # uesave 會輸出 StructType 警告到 stderr，可以忽略
                logger.debug(
                    "uesave stderr (warnings): %s",
                    stderr.decode("utf-8", errors="replace")[:500],
                )

            json_size = self._save_json_path.stat().st_size
            logger.info("uesave to-json complete: output=%d bytes", json_size)
            return True

        except FileNotFoundError:
            logger.error("uesave binary not found")
            self._uesave_available = False
            return False
        except Exception:
            logger.exception("uesave subprocess error")
            return False

    async def _run_extractor(self) -> bool:
        """執行 save_extractor 子程序（記憶體隔離）。"""
        logger.info("Running save extractor subprocess")

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "humanitz_bot.save_extractor",
                str(self._save_json_path),
                str(self._extract_json_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logger.error("Subprocess timed out after 120s: save_extractor")
                return False

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.error(
                    "Save extractor failed (rc=%d): %s", proc.returncode, err_msg
                )
                return False

            # 記錄 extractor 的 log 輸出
            if stderr:
                for line in (
                    stderr.decode("utf-8", errors="replace").strip().split("\n")
                ):
                    if line.strip():
                        logger.debug("extractor: %s", line.strip())

            logger.info(
                "Save extraction complete: %s (%d bytes)",
                self._extract_json_path,
                self._extract_json_path.stat().st_size,
            )
            return True

        except Exception:
            logger.exception("Save extractor subprocess error")
            return False

    def _import_to_db(self) -> int:
        """將提取的 JSON 匯入 SQLite（同步，在 to_thread 中執行）。

        Returns:
            成功匯入的玩家數量。
        """
        with open(self._extract_json_path, encoding="utf-8") as f:
            data = json.load(f)

        # 匯入玩家資料
        players = data.get("players", [])
        success_count = 0
        for p in players:
            try:
                # 將 challenges dict 序列化為 JSON 字串
                challenges_raw = p.get("challenges", {})
                challenges_str = json.dumps(challenges_raw, ensure_ascii=False) if challenges_raw else "{}"
                self._db.upsert_save_player(
                    steam_id=p["steam_id"],
                    x=p.get("x", 0.0),
                    y=p.get("y", 0.0),
                    z=p.get("z", 0.0),
                    health=p.get("health", 0.0),
                    hunger=p.get("hunger", 0.0),
                    thirst=p.get("thirst", 0.0),
                    stamina=p.get("stamina", 0.0),
                    infection=p.get("infection", 0.0),
                    bites=p.get("bites", 0),
                    survival_days=p.get("survival_days", 0),
                    profession=p.get("profession", ""),
                    is_male=p.get("is_male", True),
                    zombies_killed=p.get("zombies_killed", 0),
                    headshots=p.get("headshots", 0),
                    melee_kills=p.get("melee_kills", 0),
                    gun_kills=p.get("gun_kills", 0),
                    blast_kills=p.get("blast_kills", 0),
                    fist_kills=p.get("fist_kills", 0),
                    vehicle_kills=p.get("vehicle_kills", 0),
                    takedown_kills=p.get("takedown_kills", 0),
                    fish_caught=p.get("fish_caught", 0),
                    times_bitten=p.get("times_bitten", 0),
                    challenges_json=challenges_str,
                )
                success_count += 1
            except Exception:
                steam_id = p.get("steam_id", "unknown")
                logger.warning("Failed to upsert player %s: skipping", steam_id, exc_info=True)
                continue

        # 匯入遊戲狀態
        game_state = data.get("game_state", {})
        self._db.upsert_save_game_state(
            days_passed=game_state.get("days_passed", 0),
            season_day=game_state.get("season_day", 0),
            random_seed=game_state.get("random_seed", 0),
        )

        logger.debug("Extracted player_count=%d from JSON, imported=%d", len(players), success_count)
        logger.info("Imported %d/%d players and game state to database", success_count, len(players))
        return success_count

    # === 查詢 API ===

    async def get_player(self, steam_id: str) -> SavePlayerData | None:
        """查詢單個玩家的存檔資料。"""
        row = await asyncio.to_thread(self._db.get_save_player, steam_id)
        if row is None:
            return None
        return self._row_to_player(row)

    async def get_leaderboard(self, limit: int = 10) -> list[SavePlayerData]:
        """取得存活天數排行榜。"""
        rows = await asyncio.to_thread(self._db.get_save_leaderboard, limit)
        return [self._row_to_player(r) for r in rows]

    async def get_kill_leaderboard(self, limit: int = 10) -> list[SavePlayerData]:
        """取得擊殺數排行榜。"""
        rows = await asyncio.to_thread(self._db.get_kill_leaderboard, limit)
        return [self._row_to_player(r) for r in rows]

    async def get_game_state(self) -> SaveGameState | None:
        """查詢遊戲狀態。"""
        row = await asyncio.to_thread(self._db.get_save_game_state)
        if row is None:
            return None
        return SaveGameState(
            days_passed=row.get("days_passed", 0),
            season_day=row.get("season_day", 0),
            random_seed=row.get("random_seed", 0),
        )

    async def get_parse_meta(self) -> dict | None:
        """取得最近一次解析的 meta 資訊。"""
        return await asyncio.to_thread(self._db.get_save_meta)

    @staticmethod
    def _row_to_player(row: dict) -> SavePlayerData:
        """將 SQLite row dict 轉為 SavePlayerData。"""
        # 解析 challenges_json
        challenges_str = row.get("challenges_json", "{}")
        try:
            challenges = json.loads(challenges_str) if challenges_str else {}
        except (json.JSONDecodeError, TypeError):
            challenges = {}

        return SavePlayerData(
            steam_id=row.get("steam_id", ""),
            x=row.get("x", 0.0),
            y=row.get("y", 0.0),
            z=row.get("z", 0.0),
            health=row.get("health", 0.0),
            hunger=row.get("hunger", 0.0),
            thirst=row.get("thirst", 0.0),
            stamina=row.get("stamina", 0.0),
            infection=row.get("infection", 0.0),
            bites=row.get("bites", 0),
            survival_days=row.get("survival_days", 0),
            profession=row.get("profession", ""),
            is_male=bool(row.get("is_male", 1)),
            zombies_killed=row.get("zombies_killed", 0),
            headshots=row.get("headshots", 0),
            melee_kills=row.get("melee_kills", 0),
            gun_kills=row.get("gun_kills", 0),
            blast_kills=row.get("blast_kills", 0),
            fist_kills=row.get("fist_kills", 0),
            vehicle_kills=row.get("vehicle_kills", 0),
            takedown_kills=row.get("takedown_kills", 0),
            fish_caught=row.get("fish_caught", 0),
            times_bitten=row.get("times_bitten", 0),
            challenges=challenges,
        )
