"""玩家身份對應服務 — 維護玩家名稱與 SteamID 的雙向映射。

透過 RCON Players 指令定期更新在線玩家的 name↔SteamID 對應，
並持久化到 SQLite 以便跨重啟保留歷史記錄。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from humanitz_bot.services.database import Database

logger = logging.getLogger("humanitz_bot.services.player_identity")


@dataclass
class PlayerIdentityInfo:
    """玩家身份資訊"""

    steam_id: str
    player_name: str
    eos_id: str = ""


class PlayerIdentityService:
    """管理玩家名稱與 SteamID 的對應關係。

    記憶體中維護快取，同時持久化到 SQLite。
    由 ServerStatusCog 在每次 fetch_all() 後餵入最新的在線玩家資料。
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        # 記憶體快取：name → steam_id（小寫 name 作為 key）
        self._name_to_steam: dict[str, str] = {}
        # 記憶體快取：steam_id → name
        self._steam_to_name: dict[str, str] = {}
        self._load_from_db()

    def _load_from_db(self) -> None:
        """從 SQLite 載入已知的玩家身份到記憶體快取。"""
        try:
            identities = self._db.get_all_player_identities()
            for row in identities:
                name = row["player_name"]
                steam_id = row["steam_id"]
                self._name_to_steam[name.lower()] = steam_id
                self._steam_to_name[steam_id] = name
            logger.info("Loaded %d player identities from database", len(identities))
        except Exception:
            logger.exception("Failed to load player identities from database")

    def update_players(self, players: list[PlayerIdentityInfo]) -> None:
        """批次更新玩家身份（來自 RCON Players 指令結果）。

        Args:
            players: 從 RconService.fetch_all() 解析出的玩家列表
        """
        for p in players:
            if not p.steam_id or not p.player_name:
                continue

            # 更新記憶體快取（先清除舊名稱的映射）
            old_name = self._steam_to_name.get(p.steam_id)
            if old_name and old_name != p.player_name:
                self._name_to_steam.pop(old_name.lower(), None)
            self._steam_to_name[p.steam_id] = p.player_name
            self._name_to_steam[p.player_name.lower()] = p.steam_id

            # 持久化到 SQLite
            try:
                self._db.upsert_player_identity(
                    steam_id=p.steam_id,
                    player_name=p.player_name,
                    eos_id=p.eos_id,
                )
            except Exception:
                logger.exception("Failed to upsert player identity: %s", p.player_name)

        if players:
            logger.debug("Updated %d player identities", len(players))

    def get_steam_id(self, player_name: str) -> str | None:
        """根據玩家名稱取得 SteamID。

        Args:
            player_name: 玩家顯示名稱（大小寫不敏感）

        Returns:
            SteamID 字串，或 None（找不到時）
        """
        # 先查記憶體快取
        steam_id = self._name_to_steam.get(player_name.lower())
        if steam_id is not None:
            return steam_id

        # 再查 SQLite（大小寫不敏感，與快取行為一致）
        try:
            with self._db._lock:
                conn = self._db._get_conn()
                try:
                    row = conn.execute(
                        "SELECT steam_id, player_name FROM player_identity"
                        " WHERE player_name = ? COLLATE NOCASE",
                        (player_name,),
                    ).fetchone()
                finally:
                    conn.close()
            if row is not None:
                # 同步更新記憶體快取
                self._name_to_steam[row["player_name"].lower()] = row["steam_id"]
                self._steam_to_name[row["steam_id"]] = row["player_name"]
                return row["steam_id"]
        except Exception:
            logger.exception("Failed to query player identity: %s", player_name)
        return None

    def get_player_name(self, steam_id: str) -> str | None:
        """根據 SteamID 取得玩家名稱。

        Args:
            steam_id: Steam64 ID

        Returns:
            玩家名稱字串，或 None（找不到時）
        """
        # 先查記憶體快取
        name = self._steam_to_name.get(steam_id)
        if name is not None:
            return name

        # 再查 SQLite（可能是其他程序寫入的）
        try:
            result = self._db.get_player_name_by_steam_id(steam_id)
            if result is not None:
                # 同步更新記憶體快取
                self._steam_to_name[steam_id] = result
                self._name_to_steam[result.lower()] = steam_id
                return result
        except Exception:
            logger.exception("Failed to query player identity by steam_id: %s", steam_id)

        return None

    @property
    def known_count(self) -> int:
        """已知的玩家身份數量。"""
        return len(self._steam_to_name)

    def import_from_connected_log(self, log_path: str) -> int:
        """從 PlayerConnectedLog.txt 匯入歷史玩家身份對應。

        解析每一行 Connected/Disconnected 記錄，擷取 name↔SteamID↔EosID 並寫入
        記憶體快取與 SQLite。相同 SteamID 會取最新的名稱（檔案尾端 = 最新）。

        Args:
            log_path: PlayerConnectedLog.txt 的檔案路徑

        Returns:
            匯入的不重複玩家數量
        """
        path = Path(log_path)
        if not path.exists():
            logger.warning("PlayerConnectedLog not found: %s", path)
            return 0

        # 匹配: Player Connected/Disconnected <name> NetID(<steam64>_+_|<eosid>) (<date>)
        pattern = re.compile(
            r"^Player (?:Connected|Disconnected) (.+?) "
            r"NetID\((\d+)_\+_\|([a-fA-F0-9]+)\)"
        )

        # 收集所有 steam_id → (name, eos_id)，後面的行會覆蓋前面的（取最新名稱）
        identities: dict[str, tuple[str, str]] = {}

        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    m = pattern.match(line)
                    if not m:
                        continue
                    name = m.group(1)
                    steam_id = m.group(2)
                    eos_id = m.group(3)
                    identities[steam_id] = (name, eos_id)
        except OSError as e:
            logger.error("Failed to read PlayerConnectedLog: %s", e)
            return 0

        if not identities:
            logger.info("No player identities found in log file")
            return 0

        # 批次寫入快取與 DB
        imported = 0
        for steam_id, (name, eos_id) in identities.items():
            # 更新記憶體快取（先清除舊名稱的映射）
            old_name = self._steam_to_name.get(steam_id)
            if old_name and old_name != name:
                self._name_to_steam.pop(old_name.lower(), None)
            self._steam_to_name[steam_id] = name
            self._name_to_steam[name.lower()] = steam_id

            # 持久化到 SQLite
            try:
                self._db.upsert_player_identity(
                    steam_id=steam_id,
                    player_name=name,
                    eos_id=eos_id,
                )
                imported += 1
            except Exception:
                logger.exception("Failed to import identity: %s (%s)", name, steam_id)

        logger.info(
            "Imported %d player identities from PlayerConnectedLog (%d total known)",
            imported,
            self.known_count,
        )
        return imported

    def import_from_mapped_file(self, file_path: str) -> int:
        """從 PlayerIDMapped.txt 匯入玩家身份對應（遊戲伺服器權威來源）。

        格式: <steam64>_+_|<eosid>@<player_name>
        每行一位玩家，不重複。

        Args:
            file_path: PlayerIDMapped.txt 的檔案路徑

        Returns:
            匯入的不重複玩家數量
        """
        path = Path(file_path)
        if not path.exists():
            logger.debug("PlayerIDMapped.txt not found: %s", path)
            return 0

        identities: list[tuple[str, str, str]] = []  # (steam_id, eos_id, name)

        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # 格式: <steam64>_+_|<eosid>@<name>
                    at_idx = line.find("@")
                    if at_idx == -1:
                        continue
                    id_part = line[:at_idx]
                    name = line[at_idx + 1:]
                    if not name:
                        continue
                    # id_part: <steam64>_+_|<eosid>
                    sep = id_part.find("_+_|")
                    if sep == -1:
                        continue
                    steam_id = id_part[:sep]
                    eos_id = id_part[sep + 4:]
                    if not steam_id.isdigit():
                        continue
                    identities.append((steam_id, eos_id, name))
        except OSError as e:
            logger.error("Failed to read PlayerIDMapped.txt: %s", e)
            return 0

        if not identities:
            logger.info("No player identities found in mapped file")
            return 0

        imported = 0
        for steam_id, eos_id, name in identities:
            old_name = self._steam_to_name.get(steam_id)
            if old_name and old_name != name:
                self._name_to_steam.pop(old_name.lower(), None)
            self._steam_to_name[steam_id] = name
            self._name_to_steam[name.lower()] = steam_id

            try:
                self._db.upsert_player_identity(
                    steam_id=steam_id,
                    player_name=name,
                    eos_id=eos_id,
                )
                imported += 1
            except Exception:
                logger.exception("Failed to import identity: %s (%s)", name, steam_id)

        logger.info(
            "Imported %d player identities from PlayerIDMapped.txt (%d total known)",
            imported,
            self.known_count,
        )
        return imported
