"""Lightweight save-cache reader.

This service intentionally never parses the HumanitZ .sav file. The cache is
generated out-of-process by scripts/save-cache-lite.js on a low-frequency
schedule, then the bot reads the compact JSON snapshot here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("humanitz_bot.services.save_cache")


@dataclass(slots=True)
class CachedSavePlayer:
    steam_id: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    health: int = 0
    hunger: int = 0
    thirst: int = 0
    stamina: int = 0
    infection: int = 0
    bites: int = 0
    survival_days: int = 0
    profession: str = ""
    is_male: bool = True
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
    player_name: str = ""


@dataclass(slots=True)
class CachedSaveGameState:
    days_passed: int = 0
    season_day: int = 0
    random_seed: int = 0


class SaveCacheService:
    """Read-only API compatible with the game command handlers."""

    def __init__(self, cache_path: str, max_age_seconds: int) -> None:
        self._cache_path = Path(cache_path)
        self._max_age_seconds = max_age_seconds
        self._mtime: float | None = None
        self._data: dict[str, Any] | None = None
        self._last_error: str | None = None

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    @property
    def is_available(self) -> bool:
        data = self._load()
        return data is not None and not self._is_stale(data)

    async def get_player(self, steam_id: str) -> CachedSavePlayer | None:
        return await asyncio.to_thread(self._get_player_sync, steam_id)

    async def get_leaderboard(self, limit: int = 10) -> list[CachedSavePlayer]:
        return await asyncio.to_thread(
            self._get_leaderboard_sync, "survivalDays", limit
        )

    async def get_kill_leaderboard(self, limit: int = 10) -> list[CachedSavePlayer]:
        return await asyncio.to_thread(self._get_leaderboard_sync, "kills", limit)

    async def get_game_state(self) -> CachedSaveGameState | None:
        return await asyncio.to_thread(self._get_game_state_sync)

    async def get_parse_meta(self) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_parse_meta_sync)

    def _load(self) -> dict[str, Any] | None:
        try:
            stat = self._cache_path.stat()
        except FileNotFoundError:
            self._last_error = f"cache file not found: {self._cache_path}"
            return None
        except OSError as exc:
            self._last_error = f"cache stat failed: {exc}"
            logger.warning("%s", self._last_error)
            return None

        if self._data is not None and self._mtime == stat.st_mtime:
            return self._data

        try:
            with self._cache_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            self._last_error = f"cache read failed: {exc}"
            logger.warning("%s", self._last_error)
            return None

        if not isinstance(data, dict):
            self._last_error = "cache root is not an object"
            logger.warning("%s", self._last_error)
            return None

        self._data = data
        self._mtime = stat.st_mtime
        self._last_error = None
        return data

    def _is_stale(self, data: dict[str, Any]) -> bool:
        parsed_at = data.get("parsedAt")
        if not isinstance(parsed_at, str) or not parsed_at:
            return True
        try:
            parsed_ts = datetime.fromisoformat(
                parsed_at.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            return True
        return (time.time() - parsed_ts) > self._max_age_seconds

    def _get_player_sync(self, steam_id: str) -> CachedSavePlayer | None:
        data = self._load()
        if data is None or self._is_stale(data):
            return None
        players = data.get("players")
        if not isinstance(players, dict):
            return None
        raw = players.get(steam_id)
        if not isinstance(raw, dict):
            return None
        return self._player_from_raw(raw)

    def _get_leaderboard_sync(
        self, board_name: str, limit: int
    ) -> list[CachedSavePlayer]:
        data = self._load()
        if data is None or self._is_stale(data):
            return []
        leaderboards = data.get("leaderboards")
        if not isinstance(leaderboards, dict):
            return []
        rows = leaderboards.get(board_name)
        if not isinstance(rows, list):
            return []
        players: list[CachedSavePlayer] = []
        for raw in rows[:limit]:
            if isinstance(raw, dict):
                players.append(self._player_from_raw(raw))
        return players

    def _get_game_state_sync(self) -> CachedSaveGameState | None:
        data = self._load()
        if data is None or self._is_stale(data):
            return None
        raw = data.get("worldState")
        if not isinstance(raw, dict):
            return None
        return CachedSaveGameState(
            days_passed=self._to_int(raw.get("daysPassed")),
            season_day=self._to_int(raw.get("seasonDay")),
        )

    def _get_parse_meta_sync(self) -> dict[str, Any] | None:
        data = self._load()
        if data is None:
            return None
        return {
            "last_parse_time": data.get("parsedAt"),
            "parse_duration": self._to_float(data.get("parseDurationMs")) / 1000,
            "save_file_mtime": data.get("saveFileMtime"),
            "player_count": self._to_int(data.get("playerCount")),
            "cache_path": str(self._cache_path),
            "cache_stale": self._is_stale(data),
        }

    @classmethod
    def _player_from_raw(cls, raw: dict[str, Any]) -> CachedSavePlayer:
        return CachedSavePlayer(
            steam_id=str(raw.get("steamId") or ""),
            x=cls._to_float(raw.get("x")),
            y=cls._to_float(raw.get("y")),
            z=cls._to_float(raw.get("z")),
            health=cls._to_int(raw.get("health")),
            hunger=cls._to_int(raw.get("hunger")),
            thirst=cls._to_int(raw.get("thirst")),
            stamina=cls._to_int(raw.get("stamina")),
            infection=cls._to_int(raw.get("infection")),
            bites=cls._to_int(raw.get("bites")),
            survival_days=cls._to_int(raw.get("survivalDays")),
            profession=str(raw.get("profession") or ""),
            is_male=bool(raw.get("isMale", True)),
            zombies_killed=cls._to_int(raw.get("zombiesKilled")),
            headshots=cls._to_int(raw.get("headshots")),
            melee_kills=cls._to_int(raw.get("meleeKills")),
            gun_kills=cls._to_int(raw.get("gunKills")),
            blast_kills=cls._to_int(raw.get("blastKills")),
            fist_kills=cls._to_int(raw.get("fistKills")),
            vehicle_kills=cls._to_int(raw.get("vehicleKills")),
            takedown_kills=cls._to_int(raw.get("takedownKills")),
            fish_caught=cls._to_int(raw.get("fishCaught")),
            times_bitten=cls._to_int(raw.get("timesBitten")),
        )

    @staticmethod
    def _to_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0
