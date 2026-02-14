"""玩家連線追蹤服務 — 解析 PlayerConnectedLog.txt 計算在線時長。"""

from __future__ import annotations

import logging
import re
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from humanitz_bot.utils.i18n import t

logger = logging.getLogger("humanitz_bot.services.player_tracker")

# 匹配格式: Player Connected NAME NetID(STEAMID_+_|EOSID) (DD/MM/Y,YYY HH:MM)
_CONNECTED_RE = re.compile(
    r"^Player Connected (.+?) NetID\(.+?\) \((\d+/\d+/[\d,]+)\s+(\d+:\d+)\)$"
)

_TAIL_LINES = 200


class PlayerTracker:
    """解析 PlayerConnectedLog.txt，取得指定玩家的最近連線時間。"""

    def __init__(self, log_path: str) -> None:
        self._log_path = Path(log_path)

    def get_online_times(self, online_names: list[str]) -> dict[str, datetime]:
        """取得指定在線玩家的最近 Connected 時間。

        僅讀取日誌尾端約 200 行以提升效能。
        從最後一行往前搜尋，找到每位玩家最近一次 Connected 記錄。

        Args:
            online_names: 目前在線的玩家名稱列表。

        Returns:
            dict，key 為玩家名稱，value 為連線時間 datetime。
            若在日誌尾端找不到該玩家的記錄則不包含。
        """
        if not self._log_path.exists():
            logger.warning(t("log.player_log_not_found"), self._log_path)
            return {}

        if not online_names:
            return {}

        try:
            with open(self._log_path, encoding="utf-8", errors="replace") as f:
                tail_lines = list(deque(f, maxlen=_TAIL_LINES))
        except OSError as e:
            logger.error(t("log.player_log_read_error"), e)
            return {}

        remaining = set(online_names)
        result: dict[str, datetime] = {}

        for line in reversed(tail_lines):
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

            # 解析日期 — 年份有逗號: 2,026 → 2026
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
