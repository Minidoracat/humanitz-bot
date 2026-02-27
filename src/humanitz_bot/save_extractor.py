"""存檔資料提取器 — 作為獨立子程序執行，避免主程序記憶體暴漲。

用法：
    python -m humanitz_bot.save_extractor <input_json> <output_json>

從 uesave 產出的完整 JSON（~280MB）中提取玩家資料與遊戲狀態，
輸出精簡 JSON（~數百 KB）供主程序快速讀取。

此腳本會在獨立程序中載入完整 JSON，提取後立即退出並釋放記憶體。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] save_extractor: %(message)s",
)
logger = logging.getLogger("save_extractor")

def _safe_int(val: object, default: int = 0) -> int:
    """安全的 int 轉換，轉換失敗時回傳預設值。"""
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_float(val: object, default: float = 0.0) -> float:
    """安全的 float 轉換，轉換失敗時回傳預設值。"""
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def _find_value(player: dict, prefix: str) -> object:
    """在玩家 dict 中找到以指定前綴開頭的 key 並返回其值。"""
    for key, value in player.items():
        if key.startswith(prefix):
            return value
    return None


def _extract_game_stats(player: dict) -> dict:
    """提取 GameStats_66_ 擊殺統計。

    GameStats_66_ 是一個 list，每個元素為 {"key": str, "value": int}。
    """
    game_stats_raw = _find_value(player, "GameStats_66_")
    stats: dict[str, int] = {}

    if isinstance(game_stats_raw, list):
        for item in game_stats_raw:
            if isinstance(item, dict) and "key" in item and "value" in item:
                stats[item["key"]] = item["value"]

    return {
        "zombies_killed": stats.get("ZeeksKilled", 0),
        "headshots": stats.get("HeadShot", 0),
        "melee_kills": stats.get("MeleeKills", 0),
        "gun_kills": stats.get("GunKills", 0),
        "blast_kills": stats.get("BlastKills", 0),
        "fist_kills": stats.get("FistKills", 0),
        "vehicle_kills": stats.get("VehicleKills", 0),
        "takedown_kills": stats.get("TakedownKills", 0),
        "fish_caught": stats.get("CaughtFish", 0),
        "times_bitten": stats.get("TimesBitten", 0),
    }


def _extract_statistics(player: dict) -> dict:
    """提取 Statistics_93_ 挑戰與進度統計。

    Statistics_93_ 是一個 list，每個元素含 StatisticId (tag) 與 CurrentValue。
    """
    stats_raw = _find_value(player, "Statistics_93_")
    challenges: dict[str, float] = {}

    if isinstance(stats_raw, list):
        for item in stats_raw:
            if not isinstance(item, dict):
                continue
            # 從 key 中找 tag name 和 value
            tag_name = ""
            current_value = 0.0
            for k, v in item.items():
                if k.startswith("StatisticId_") and isinstance(v, dict):
                    tag_name = v.get("TagName_0", "")
                elif k.startswith("CurrentValue_"):
                    current_value = _safe_float(v)
            if tag_name:
                # 簡化 tag 名稱：statistics.stat.challenge.KillSomeZombies → challenge.KillSomeZombies
                short_tag = tag_name
                if short_tag.startswith("statistics.stat."):
                    short_tag = short_tag[len("statistics.stat."):]
                challenges[short_tag] = current_value

    return challenges
def _extract_player(player: dict) -> dict | None:
    """從單個玩家原始資料中提取關鍵欄位。"""
    steam_id_raw = _find_value(player, "SteamID_67_")
    if not steam_id_raw or not isinstance(steam_id_raw, str):
        return None

    # 提取 Steam64 ID（格式："76561198033176898_+_|eosid"）
    steam_id = steam_id_raw.split("_+_")[0] if "_+_" in steam_id_raw else steam_id_raw

    # 座標
    transform = _find_value(player, "PlayerTransform_35_")
    x, y, z = 0.0, 0.0, 0.0
    if isinstance(transform, dict):
        translation = transform.get("Translation_0", {})
        if isinstance(translation, dict):
            x = translation.get("x", 0.0)
            y = translation.get("y", 0.0)
            z = translation.get("z", 0.0)

    # 狀態值
    health = _find_value(player, "CurrentHealth_6_")
    hunger = _find_value(player, "CurrentHunger_14_")
    thirst = _find_value(player, "CurrentThirst_10_")
    stamina = _find_value(player, "CurrentStamina_18_")
    infection = _find_value(player, "CurrentInfection_24_")
    bites = _find_value(player, "Bites_29_")
    survival_days = _find_value(player, "DayzSurvived_105_")
    profession_raw = _find_value(player, "StartingPerk_94_")
    is_male = _find_value(player, "Male_59_")

    # 清理職業名稱（"Enum_Professions::NewEnumerator17" → "NewEnumerator17"）
    profession = ""
    if isinstance(profession_raw, str) and "::" in profession_raw:
        profession = profession_raw.split("::")[-1]
    elif isinstance(profession_raw, str):
        profession = profession_raw

    # === 擊殺統計（GameStats_66_）===
    kill_stats = _extract_game_stats(player)

    # === 挑戰/進度統計（Statistics_93_）===
    challenges = _extract_statistics(player)

    result = {
        "steam_id": steam_id,
        "x": _safe_float(x),
        "y": _safe_float(y),
        "z": _safe_float(z),
        "health": _safe_float(health),
        "hunger": _safe_float(hunger),
        "thirst": _safe_float(thirst),
        "stamina": _safe_float(stamina),
        "infection": _safe_float(infection),
        "bites": _safe_int(bites),
        "survival_days": _safe_int(survival_days),
        "profession": profession,
        "is_male": bool(is_male) if is_male is not None else True,
    }
    result.update(kill_stats)
    result["challenges"] = challenges

    return result


def extract(input_path: str, output_path: str) -> None:
    """從完整 JSON 提取玩家資料與遊戲狀態。"""
    start = time.monotonic()

    logger.info("Loading JSON: %s", input_path)
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    load_elapsed = time.monotonic() - start
    logger.info("JSON loaded in %.1fs", load_elapsed)

    props = data.get("root", {}).get("properties", {})

    # 提取玩家資料
    players_raw = props.get("DropInSaves_0", [])
    players = []
    for p in players_raw:
        if not isinstance(p, dict):
            continue
        try:
            extracted = _extract_player(p)
            if extracted is not None:
                players.append(extracted)
        except Exception as e:
            steam_id_raw = _find_value(p, "SteamID_67_") if isinstance(p, dict) else "unknown"
            print(f"Warning: Failed to extract player {steam_id_raw}: {e}", file=sys.stderr)
            continue

    logger.info(
        "Extracted %d players from %d raw entries", len(players), len(players_raw)
    )

    # 提取遊戲狀態
    days_passed = props.get("Dedi_DaysPassed_0", 0)
    season_day = props.get("CurrentSeasonDay_0", 0)
    random_seed = props.get("RandomSeed_0", 0)

    # 確保類型正確
    if not isinstance(days_passed, int):
        days_passed = _safe_int(days_passed)
    if not isinstance(season_day, int):
        season_day = _safe_int(season_day)
    if not isinstance(random_seed, int):
        random_seed = _safe_int(random_seed)

    result = {
        "players": players,
        "game_state": {
            "days_passed": days_passed,
            "season_day": season_day,
            "random_seed": random_seed,
        },
        "meta": {
            "player_count": len(players),
            "extract_time": time.time(),
            "extract_duration": time.monotonic() - start,
        },
    }

    # 寫入精簡 JSON
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)

    elapsed = time.monotonic() - start
    output_size = output.stat().st_size
    logger.info(
        "Extraction complete: %d players, output=%d bytes, total=%.1fs",
        len(players),
        output_size,
        elapsed,
    )


def main() -> None:
    """子程序進入點。"""
    if len(sys.argv) != 3:
        print(
            f"Usage: {sys.argv[0]} <input_json_path> <output_json_path>",
            file=sys.stderr,
        )
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    if not Path(input_path).exists():
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)

    try:
        extract(input_path, output_path)
    except Exception:
        logger.exception("Extraction failed")
        sys.exit(2)


if __name__ == "__main__":
    main()
