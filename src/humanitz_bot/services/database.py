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
"""


class Database:
    def __init__(self, data_dir: str = "data", retention_days: int = 30) -> None:
        self._db_path = Path(data_dir) / _DB_FILENAME
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._retention = timedelta(days=retention_days)
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
                logger.info("Database initialized: %s", self._db_path)
            finally:
                conn.close()

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
                conn.commit()
                if total > 0:
                    logger.info("Pruned %d old records (cutoff: %s)", total, cutoff)
            finally:
                conn.close()
        return total
