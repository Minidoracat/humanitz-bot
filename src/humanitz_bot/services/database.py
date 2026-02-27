from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("humanitz_bot.services.database")

_DB_FILENAME = "humanitz_bot.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS player_count (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT    NOT NULL,
    count     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    event_type  TEXT    NOT NULL,
    player_name TEXT    NOT NULL DEFAULT '',
    message     TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS player_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    player_name TEXT    NOT NULL,
    event_type  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_player_count_ts ON player_count(timestamp);
CREATE INDEX IF NOT EXISTS idx_chat_log_ts ON chat_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_player_sessions_ts ON player_sessions(timestamp);
CREATE INDEX IF NOT EXISTS idx_player_sessions_name ON player_sessions(player_name);

CREATE TABLE IF NOT EXISTS save_players (
    steam_id        TEXT    PRIMARY KEY,
    x               REAL    NOT NULL DEFAULT 0,
    y               REAL    NOT NULL DEFAULT 0,
    z               REAL    NOT NULL DEFAULT 0,
    health          REAL    NOT NULL DEFAULT 0,
    hunger          REAL    NOT NULL DEFAULT 0,
    thirst          REAL    NOT NULL DEFAULT 0,
    stamina         REAL    NOT NULL DEFAULT 0,
    infection       REAL    NOT NULL DEFAULT 0,
    bites           INTEGER NOT NULL DEFAULT 0,
    survival_days   INTEGER NOT NULL DEFAULT 0,
    profession      TEXT    NOT NULL DEFAULT '',
    is_male         INTEGER NOT NULL DEFAULT 1,
    zombies_killed  INTEGER NOT NULL DEFAULT 0,
    headshots       INTEGER NOT NULL DEFAULT 0,
    melee_kills     INTEGER NOT NULL DEFAULT 0,
    gun_kills       INTEGER NOT NULL DEFAULT 0,
    blast_kills     INTEGER NOT NULL DEFAULT 0,
    fist_kills      INTEGER NOT NULL DEFAULT 0,
    vehicle_kills   INTEGER NOT NULL DEFAULT 0,
    takedown_kills  INTEGER NOT NULL DEFAULT 0,
    fish_caught     INTEGER NOT NULL DEFAULT 0,
    times_bitten    INTEGER NOT NULL DEFAULT 0,
    challenges_json TEXT    NOT NULL DEFAULT '{}',
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS save_game_state (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    days_passed   INTEGER NOT NULL DEFAULT 0,
    season_day    INTEGER NOT NULL DEFAULT 0,
    random_seed   INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS save_meta (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    last_parse_time TEXT,
    parse_duration  REAL    NOT NULL DEFAULT 0,
    save_file_mtime TEXT,
    player_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS player_identity (
    steam_id    TEXT    PRIMARY KEY,
    player_name TEXT    NOT NULL,
    eos_id      TEXT    NOT NULL DEFAULT '',
    updated_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_player_identity_name ON player_identity(player_name);
CREATE INDEX IF NOT EXISTS idx_save_players_days ON save_players(survival_days DESC);
CREATE INDEX IF NOT EXISTS idx_save_players_kills ON save_players(zombies_killed DESC);
"""


class Database:
    def __init__(self, data_dir: str = "data", retention_days: int = 30) -> None:
        self._db_path = Path(data_dir) / _DB_FILENAME
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._retention = timedelta(days=retention_days)
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """建立 SQLite 連線（不含 PRAGMA，PRAGMA 在 _init_db 設定一次）。"""
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                # WAL 模式與 synchronous 只需設定一次（持久性設定）
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                # Migration 必須在 executescript 之前執行，
                # 因為 _SCHEMA 中的 CREATE INDEX 引用了新欄位。
                self._migrate_save_players(conn)
                conn.executescript(_SCHEMA)
                conn.commit()
                logger.info("Database initialized: %s", self._db_path)
            finally:
                conn.close()

    def _migrate_save_players(self, conn: sqlite3.Connection) -> None:
        """Schema migration — 為現有 save_players 資料表新增擊殺/挑戰欄位。

        全新 DB 時 table 還不存在，直接跳過（後續 executescript 會建立完整 schema）。
        """
        # 檢查 save_players table 是否存在
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='save_players'"
        )
        if cursor.fetchone() is None:
            return  # 全新 DB，無需 migration

        _NEW_COLUMNS = [
            ("zombies_killed", "INTEGER NOT NULL DEFAULT 0"),
            ("headshots", "INTEGER NOT NULL DEFAULT 0"),
            ("melee_kills", "INTEGER NOT NULL DEFAULT 0"),
            ("gun_kills", "INTEGER NOT NULL DEFAULT 0"),
            ("blast_kills", "INTEGER NOT NULL DEFAULT 0"),
            ("fist_kills", "INTEGER NOT NULL DEFAULT 0"),
            ("vehicle_kills", "INTEGER NOT NULL DEFAULT 0"),
            ("takedown_kills", "INTEGER NOT NULL DEFAULT 0"),
            ("fish_caught", "INTEGER NOT NULL DEFAULT 0"),
            ("times_bitten", "INTEGER NOT NULL DEFAULT 0"),
            ("challenges_json", "TEXT NOT NULL DEFAULT '{}'"),
        ]
        for col_name, col_def in _NEW_COLUMNS:
            try:
                conn.execute(
                    f"ALTER TABLE save_players ADD COLUMN {col_name} {col_def}"
                )
                logger.info("Migration: added column save_players.%s", col_name)
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    pass  # Column already exists — expected for non-first run
                else:
                    raise

    def add_player_count(self, count: int) -> None:
        ts = datetime.now().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO player_count (timestamp, count) VALUES (?, ?)",
                    (ts, count),
                )
                conn.commit()
            finally:
                conn.close()

    def get_player_count_history(self, hours: int = 24) -> list[tuple[str, int]]:
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT timestamp, count FROM player_count WHERE timestamp >= ? ORDER BY timestamp",
                    (cutoff,),
                ).fetchall()
                return [(r["timestamp"], r["count"]) for r in rows]
            finally:
                conn.close()

    def add_chat_event(
        self, event_type: str, player_name: str = "", message: str = ""
    ) -> None:
        ts = datetime.now().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO chat_log (timestamp, event_type, player_name, message) VALUES (?, ?, ?, ?)",
                    (ts, event_type, player_name, message),
                )
                conn.commit()
            finally:
                conn.close()

    def add_player_session_event(self, player_name: str, event_type: str) -> None:
        ts = datetime.now().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO player_sessions (timestamp, player_name, event_type) VALUES (?, ?, ?)",
                    (ts, player_name, event_type),
                )
                conn.commit()
            finally:
                conn.close()

    def get_death_count(self, hours: int = 24) -> int:
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM player_sessions "
                    "WHERE event_type = 'player_died' AND timestamp >= ?",
                    (cutoff,),
                ).fetchone()
                return row["cnt"] if row else 0
            finally:
                conn.close()

    def prune_old_data(self) -> int:
        cutoff = (datetime.now() - self._retention).isoformat()
        total = 0
        with self._lock:
            conn = self._get_conn()
            try:
                for table in ("player_count", "chat_log", "player_sessions"):
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,)
                    )
                    total += cursor.rowcount
                # 注意：不清除 player_identity（身份是參考資料，應永久保留）
                conn.commit()
                if total > 0:
                    logger.info("Pruned %d old records (cutoff: %s)", total, cutoff)
            finally:
                conn.close()
        return total

    def upsert_save_player(
        self,
        steam_id: str,
        x: float,
        y: float,
        z: float,
        health: float,
        hunger: float,
        thirst: float,
        stamina: float,
        infection: float,
        bites: int,
        survival_days: int,
        profession: str,
        is_male: bool,
        zombies_killed: int = 0,
        headshots: int = 0,
        melee_kills: int = 0,
        gun_kills: int = 0,
        blast_kills: int = 0,
        fist_kills: int = 0,
        vehicle_kills: int = 0,
        takedown_kills: int = 0,
        fish_caught: int = 0,
        times_bitten: int = 0,
        challenges_json: str = "{}",
    ) -> None:
        ts = datetime.now().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO save_players "
                    "(steam_id, x, y, z, health, hunger, thirst, stamina, infection, "
                    "bites, survival_days, profession, is_male, "
                    "zombies_killed, headshots, melee_kills, gun_kills, blast_kills, "
                    "fist_kills, vehicle_kills, takedown_kills, fish_caught, times_bitten, "
                    "challenges_json, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        steam_id, x, y, z, health, hunger, thirst, stamina, infection,
                        bites, survival_days, profession, int(is_male),
                        zombies_killed, headshots, melee_kills, gun_kills, blast_kills,
                        fist_kills, vehicle_kills, takedown_kills, fish_caught, times_bitten,
                        challenges_json, ts,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def upsert_save_game_state(
        self, days_passed: int, season_day: int, random_seed: int
    ) -> None:
        ts = datetime.now().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO save_game_state "
                    "(id, days_passed, season_day, random_seed, updated_at) "
                    "VALUES (1, ?, ?, ?, ?)",
                    (days_passed, season_day, random_seed, ts),
                )
                conn.commit()
            finally:
                conn.close()

    def upsert_save_meta(
        self,
        last_parse_time: str | None,
        parse_duration: float,
        save_file_mtime: str | None,
        player_count: int,
    ) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO save_meta "
                    "(id, last_parse_time, parse_duration, save_file_mtime, player_count) "
                    "VALUES (1, ?, ?, ?, ?)",
                    (last_parse_time, parse_duration, save_file_mtime, player_count),
                )
                conn.commit()
            finally:
                conn.close()

    def get_save_meta(self) -> dict | None:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM save_meta WHERE id = 1"
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def get_save_player(self, steam_id: str) -> dict | None:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM save_players WHERE steam_id = ?",
                    (steam_id,),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def get_save_leaderboard(self, limit: int = 10) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM save_players ORDER BY survival_days DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_kill_leaderboard(self, limit: int = 10) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM save_players ORDER BY zombies_killed DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_save_game_state(self) -> dict | None:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM save_game_state WHERE id = 1"
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def upsert_player_identity(
        self, steam_id: str, player_name: str, eos_id: str = ""
    ) -> None:
        ts = datetime.now().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO player_identity "
                    "(steam_id, player_name, eos_id, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (steam_id, player_name, eos_id, ts),
                )
                conn.commit()
            finally:
                conn.close()

    def get_steam_id_by_name(self, player_name: str) -> str | None:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT steam_id FROM player_identity WHERE player_name = ?",
                    (player_name,),
                ).fetchone()
                return row["steam_id"] if row else None
            finally:
                conn.close()

    def get_player_name_by_steam_id(self, steam_id: str) -> str | None:
        """根據 SteamID 查詢玩家名稱。"""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT player_name FROM player_identity WHERE steam_id = ?",
                    (steam_id,),
                ).fetchone()
                return row["player_name"] if row else None
            finally:
                conn.close()

    def get_all_player_identities(self) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM player_identity ORDER BY updated_at DESC"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
