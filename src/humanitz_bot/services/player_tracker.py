"""玩家連線追蹤服務 — 解析 PlayerConnectedLog.txt 計算在線時長。"""

from __future__ import annotations

import logging
import re
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Awaitable, Optional

import requests

from humanitz_bot.utils.i18n import t

logger = logging.getLogger("humanitz_bot.services.player_tracker")

# 匹配格式: Player Connected NAME NetID(STEAMID_+_|EOSID) (DD/MM/Y,YYY HH:MM)
_CONNECTED_RE = re.compile(
    r"^Player Connected (.+?) NetID\(.+?\) \((\d+/\d+/[\d,]+)\s+(\d+:\d+)\)$"
)

_TAIL_LINES = 200


class PlayerTracker:
    """解析 PlayerConnectedLog.txt，取得指定玩家的最近連線時間。
    若提供 Starbase 資訊，將改為透過 Bisect API 抓取檔案內容（每次完整抓取，非 tail）。
    """

    def __init__(
        self,
        log_path: str,
        starbase_fetcher: Optional[Callable[[], str]] = None,
    ) -> None:
        self._log_path = Path(log_path)
        self._starbase_fetcher = starbase_fetcher  # 若存在則使用遠端檔案來源

    def _read_local_tail(self) -> list[str]:
        if not self._log_path.exists():
            logger.warning(t("log.player_log_not_found"), self._log_path)
            return []
        try:
            with open(self._log_path, encoding="utf-8", errors="replace") as f:
                return list(deque(f, maxlen=_TAIL_LINES))
        except OSError as e:
            logger.error(t("log.player_log_read_error"), e)
            return []

    def _read_remote_full(self) -> list[str]:
        """遠端抓取完整檔案內容（Starbase），並回傳行列表。"""
        try:
            raw = self._starbase_fetcher() if self._starbase_fetcher else ""
        except Exception as e:
            logger.error("Failed to fetch remote PlayerConnectedLog via Starbase: %s", e)
            return []
        if not raw:
            return []
        # 直接解析全檔內容，不做 tail（避免遺漏）
        normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
        return [line for line in normalized.split("\n") if line]

    def get_online_times(self, online_names: list[str]) -> dict[str, datetime]:
        """取得指定在線玩家的最近 Connected 時間。"""
        if not online_names:
            return {}

        # 來源選擇：Starbase 遠端 > 本地檔案 tail
        if self._starbase_fetcher:
            lines = self._read_remote_full()
        else:
            lines = self._read_local_tail()

        if not lines:
            return {}

        remaining = set(online_names)
        result: dict[str, datetime] = {}

        # 從最後往前找最近一次 Connected
        for line in reversed(lines):
            if not remaining:
                break

            line = line.strip()
            if not line.startswith("Player Connected "):
                continue

            m = _CONNECTED_RE.match(line)
            if not m:
                continue

            name = m.group(1)
            if name not in remaining:
                continue

            date_str = m.group(2).replace(",", "")
            time_str = m.group(3)

            try:
                dt = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")
            except ValueError:
                logger.warning(t("log.player_time_parse_error"), date_str, time_str)
                continue

            result[name] = dt
            remaining.discard(name)

        if remaining:
            logger.debug(t("log.player_not_found_in_log"), remaining)

        return result


def format_duration(td: timedelta) -> str:
    """將 timedelta 格式化為人類可讀的時長字串。

    Examples:
        >>> format_duration(timedelta(hours=1, minutes=18))
        '1h18m'
        >>> format_duration(timedelta(minutes=38))
        '38m'
        >>> format_duration(timedelta(minutes=2))
        '2m'
    """
    total_minutes = int(td.total_seconds()) // 60
    if total_minutes < 0:
        total_minutes = 0

    hours, minutes = divmod(total_minutes, 60)

    if hours > 0:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"
