"""玩家連線追蹤服務 — 解析連線日誌（支援單一檔案或 HZLogs/Login/ 目錄）。"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from humanitz_bot.utils.i18n import t

logger = logging.getLogger("humanitz_bot.services.player_tracker")

# 匹配格式: Player Connected NAME NetID(STEAMID_+_|EOSID) (DD/MM/Y,YYY HH:MM)
_CONNECTED_RE = re.compile(
    r"^Player Connected (.+?) NetID\(.+?\) \((\d+/\d+/[\d,]+)\s+(\d+:\d+)\)$"
)

_TAIL_LINES = 200


def resolve_connect_logs(path_str: str) -> list[Path]:
    """解析連線日誌路徑 — 支援單一檔案、目錄或自動偵測。

    Args:
        path_str: 單一日誌檔路徑或包含 *_ConnectLog.txt 的目錄路徑

    Returns:
        依修改時間排序（最舊優先）的日誌檔 Path 列表。
        找不到任何檔案時回傳空列表。
    """
    p = Path(path_str)

    # 1. 指向既有檔案 → 單檔模式（向後相容）
    if p.is_file():
        logger.debug("Resolved connect log: single file mode — %s", p)
        return [p]

    # 2. 指向既有目錄 → 掃描 *_ConnectLog.txt
    if p.is_dir():
        files = sorted(p.glob("*_ConnectLog.txt"), key=lambda f: f.stat().st_mtime)
        logger.debug(
            "Resolved connect log: directory mode — %s (%d files)", p, len(files)
        )
        return files

    # 3. 路徑不存在 → 自動偵測 HZLogs/Login/
    for base in (p.parent, p.parent.parent):
        candidate = base / "HZLogs" / "Login"
        if candidate.is_dir():
            files = sorted(
                candidate.glob("*_ConnectLog.txt"),
                key=lambda f: f.stat().st_mtime,
            )
            if files:
                logger.debug(
                    "Resolved connect log: auto-detected — %s (%d files)",
                    candidate,
                    len(files),
                )
                return files

    logger.debug("Resolved connect log: nothing found for %s", path_str)
    return []


class PlayerTracker:
    """解析連線日誌（支援單一檔案或 HZLogs/Login/ 目錄），取得指定玩家的最近連線時間。"""

    def __init__(self, log_path: str) -> None:
        self._log_path_str = log_path

    def get_online_times(self, online_names: list[str]) -> dict[str, datetime]:
        """取得指定在線玩家的最近 Connected 時間。

        支援多檔案日誌結構：從最新檔案開始向舊檔案搜尋，
        找到所有玩家即提早結束。

        Args:
            online_names: 目前在線的玩家名稱列表。

        Returns:
            dict，key 為玩家名稱，value 為連線時間 datetime。
            若在日誌尾端找不到該玩家的記錄則不包含。
        """
        if not online_names:
            return {}

        log_files = resolve_connect_logs(self._log_path_str)
        if not log_files:
            logger.warning(t("log.player_log_not_found"), self._log_path_str)
            return {}

        remaining = set(online_names)
        result: dict[str, datetime] = {}

        # 從最新到最舊搜尋
        for log_file in reversed(log_files):
            if not remaining:
                break

            try:
                tail_lines = self._read_tail(log_file)
            except OSError as e:
                logger.error(t("log.player_log_read_error"), e)
                continue

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

    @staticmethod
    def _read_tail(path: Path) -> list[str]:
        """讀取檔案尾端約 200 行。"""
        with open(path, encoding="utf-8", errors="replace") as f:
            # seek 到檔案末尾附近，避免 O(n) 全檔掃描
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))  # 回退 64KB，足夠涵蓋 200+ 行
            if f.tell() > 0:
                f.readline()  # 跳過可能不完整的首行
            return f.readlines()[-_TAIL_LINES:]


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
