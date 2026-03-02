from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger("humanitz_bot.utils.chat_parser")


class ChatEventType(enum.Enum):
    """聊天事件類型"""

    PLAYER_CHAT = "player_chat"
    PLAYER_JOINED = "player_joined"
    PLAYER_LEFT = "player_left"
    PLAYER_DIED = "player_died"
    ADMIN_MESSAGE = "admin_message"
    UNKNOWN = "unknown"


@dataclass
class ChatEvent:
    """聊天事件資料結構"""

    event_type: ChatEventType
    player_name: str  # 玩家名稱 (admin/unknown 時為空)
    message: str  # 聊天訊息、管理員訊息,或空字串 (join/leave/death)
    raw_line: str  # 原始未解析行


# RCON 時間戳前綴: [1/3/2,026 - 14:7] — 遊戲更新 2026-03-01 起新增
# 年份千位分隔符因系統語系不同可能是逗號(2,026)或空格(2 026)
_RCON_TIMESTAMP_RE = re.compile(r"^\[\d+/\d+/[\d, ]+ - \d+:\d+\]\s*")

# 檔案時間戳前綴: (3/3/2,026 0:12) — HZLogs/Chat/ 檔案格式
_FILE_TIMESTAMP_RE = re.compile(r"^\(\d+/\d+/[\d, ]+ \d+:\d+\)\s*")

# 內嵌時間戳: 用於分割黏合的多事件行（如 Event1</>)[ts2] Event2）
# 內嵌時間戳: 用於分割黏合的多事件行（RCON 和檔案格式皆支援）
_EMBEDDED_TS_RE = re.compile(
    r"(?<=\))(?="
    r"(?:\[\d+/\d+/[\d, ]+ - \d+:\d+\]"
    r"|\(\d+/\d+/[\d, ]+ \d+:\d+\))"
    r")"
)

# 編譯正則表達式 patterns（匹配去除時間戳後的內容）
# 玩家名稱使用 [^<]* 而非 .*? 以避免跨事件回溯配對的問題
_CHAT_RE = re.compile(r"^<PN>([^<]+):</>(.+)$")
# 管理員玩家聊天: <SP>[Admin]</><PN>PlayerName:</>訊息
_ADMIN_CHAT_RE = re.compile(r"^<SP>\[Admin\]</><PN>([^<]+):</>(.+)$")
_JOIN_RE = re.compile(r"^Player Joined \(<PN>([^<]*)</>\)$")
_LEFT_RE = re.compile(r"^Player Left \(<PN>([^<]*)</>\)$")
_DIED_RE = re.compile(r"^Player died \(<PN>([^<]*)</>\)$")
_ADMIN_RE = re.compile(r"^<SP>Admin: (.+)</>$")


def _split_events(line: str) -> list[str]:
    """分割可能黏合的多事件行。

    某些邊界情況下 RCON 可能將多個事件黏合在同一行：
    [ts1] Event1</>)[ts2] Event2</>)
    用內嵌時間戳分割為獨立行。
    """
    parts = _EMBEDDED_TS_RE.split(line)
    return [p.strip() for p in parts if p.strip()]


def _strip_timestamp(line: str) -> str:
    """移除行首的時間戳前綴（支援 RCON 和檔案兩種格式）。

    RCON 格式:  [1/3/2,026 - 14:7] event_content
    檔案格式:  (3/3/2,026 0:12) event_content
    年份千位分隔符支援逗號(2,026)和空格(2 026)。
    同時相容舊格式（無時間戳）。

    Examples:
        >>> _strip_timestamp("[1/3/2,026 - 14:7] <PN>kevin:</>hi")
        '<PN>kevin:</>hi'
        >>> _strip_timestamp("(3/3/2,026 0:12) <PN>kevin:</>hi")
        '<PN>kevin:</>hi'
        >>> _strip_timestamp("<PN>kevin:</>hi")
        '<PN>kevin:</>hi'
    """
    result = _RCON_TIMESTAMP_RE.sub("", line)
    return _FILE_TIMESTAMP_RE.sub("", result)


def parse_chat_line(line: str) -> ChatEvent:
    """解析單行 fetchchat 輸出

    支援帶時間戳前綴（2026-03-01+ 新格式）與無時間戳（舊格式）的行。

    Args:
        line: fetchchat 輸出的單行文字

    Returns:
        ChatEvent: 解析後的事件物件

    Examples:
        >>> parse_chat_line("[1/3/2,026 - 14:7] <PN>kevin052926:</>嘿嘿")
        ChatEvent(event_type=ChatEventType.PLAYER_CHAT, player_name='kevin052926', ...)

        >>> parse_chat_line("[1/3/2,026 - 14:7] Player Joined (<PN>OG83</>)")
        ChatEvent(event_type=ChatEventType.PLAYER_JOINED, player_name='OG83', ...)
    """
    # 先移除時間戳前綴，再進行 pattern 匹配
    stripped = _strip_timestamp(line)

    # 管理員玩家聊天: <SP>[Admin]</><PN>PlayerName:</>MessageText
    # 必須在普通玩家聊天之前檢查，因為普通 regex 無法匹配此格式
    m = _ADMIN_CHAT_RE.match(stripped)
    if m:
        return ChatEvent(
            event_type=ChatEventType.PLAYER_CHAT,
            player_name=m.group(1),
            message=m.group(2),
            raw_line=line,
        )

    # 普通玩家聊天: <PN>PlayerName:</>MessageText
    m = _CHAT_RE.match(stripped)
    if m:
        return ChatEvent(
            event_type=ChatEventType.PLAYER_CHAT,
            player_name=m.group(1),
            message=m.group(2),
            raw_line=line,
        )
    # 玩家加入: Player Joined (<PN>PlayerName</>)
    m = _JOIN_RE.match(stripped)
    if m:
        return ChatEvent(
            event_type=ChatEventType.PLAYER_JOINED,
            player_name=m.group(1),
            message="",
            raw_line=line,
        )

    # 玩家離開: Player Left (<PN>PlayerName</>)
    m = _LEFT_RE.match(stripped)
    if m:
        return ChatEvent(
            event_type=ChatEventType.PLAYER_LEFT,
            player_name=m.group(1),
            message="",
            raw_line=line,
        )

    # 玩家死亡: Player died (<PN>PlayerName</>)
    m = _DIED_RE.match(stripped)
    if m:
        return ChatEvent(
            event_type=ChatEventType.PLAYER_DIED,
            player_name=m.group(1),
            message="",
            raw_line=line,
        )

    # 管理員訊息: <SP>Admin: MessageText</>
    m = _ADMIN_RE.match(stripped)
    if m:
        return ChatEvent(
            event_type=ChatEventType.ADMIN_MESSAGE,
            player_name="",
            message=m.group(1),
            raw_line=line,
        )

    # 未知格式
    return ChatEvent(
        event_type=ChatEventType.UNKNOWN,
        player_name="",
        message="",
        raw_line=line,
    )


class ChatDiffer:
    """追蹤 fetchchat 快照並返回新事件"""

    def __init__(self) -> None:
        self._last_lines: list[str] = []
        self._initialized: bool = False

    @staticmethod
    def _diff(old: list[str], new: list[str]) -> list[str]:
        """找出新快照相較於舊快照的新增行。

        在新快照中從尾端搜尋舊快照最後一行的位置，
        並透過比對前面連續行來驗證錨點，正確處理重複內容。
        """
        if not old or not new:
            return new

        last_old = old[-1]
        for j in range(len(new) - 1, -1, -1):
            if new[j] != last_old:
                continue
            # Verify preceding lines match to avoid false anchor on duplicates
            verified = True
            for k in range(1, len(old)):
                new_idx = j - k
                if new_idx < 0:
                    break
                if old[len(old) - 1 - k] != new[new_idx]:
                    verified = False
                    break
            if verified:
                return new[j + 1 :]

        logger.debug("No overlap found, treating all %d lines as new", len(new))
        return new

    def get_new_events(self, raw_chat: str) -> list[ChatEvent]:
        """比較當前 fetchchat 輸出與先前快照

        只返回新事件 (先前快照中未見過的行)。
        首次呼叫返回空列表 (避免將歷史紀錄轉發到 Discord)。

        Args:
            raw_chat: fetchchat 命令的原始輸出

        Returns:
            list[ChatEvent]: 新事件列表
        """
        # 正規化換行符號並分割行
        normalized = raw_chat.replace("\r\n", "\n").replace("\r", "\n")
        current_lines = [
            line.strip() for line in normalized.split("\n") if line.strip()
        ]

        # 第一次呼叫：初始化快照，返回空列表
        if not self._initialized:
            self._last_lines = current_lines
            self._initialized = True
            logger.debug("ChatDiffer initialized with %d lines", len(current_lines))
            return []

        # 空輸出：返回空列表
        if not current_lines:
            logger.debug("Empty chat output received")
            return []

        new_lines = self._diff(self._last_lines, current_lines)

        # 更新快照
        self._last_lines = current_lines

        # 解析新行（先分割可能黏合的多事件行）
        new_events: list[ChatEvent] = []
        for line in new_lines:
            for sub_line in _split_events(line):
                new_events.append(parse_chat_line(sub_line))
        logger.debug("Parsed %d new events", len(new_events))

        return new_events


class ChatLogTailer:
    """追蹤 HZLogs/Chat/ 目錄中最新的聊天日誌檔案，返回新增事件。

    使用檔案偏移量（seek offset）增量讀取，避免重讀整個檔案。
    自動處理檔案輪替（偵測新檔案出現並切換）。
    首次呼叫跳到檔案末尾，不回放歷史事件（與 ChatDiffer 行為一致）。
    """

    _CHAT_GLOB = "*_Chat.log"

    def __init__(self, chat_dir: Path) -> None:
        self._chat_dir = chat_dir
        self._current_file: Path | None = None
        self._file_offset: int = 0
        self._initialized: bool = False

    def _find_latest_file(self) -> Path | None:
        """找到最新的 *_Chat.log 檔案（按修改時間排序）。"""
        files = sorted(
            self._chat_dir.glob(self._CHAT_GLOB),
            key=lambda f: f.stat().st_mtime,
        )
        return files[-1] if files else None

    def get_new_events(self) -> list[ChatEvent]:
        """讀取自上次以來的新行並解析為事件。

        處理邏輯：
        1. 首次呼叫 → 跳到最新檔案末尾（不回放歷史）
        2. 檔案成長 → 從上次 offset 讀取新增的行
        3. 新檔案出現（輪替）→ 讀完舊檔剩餘行 → 切換到新檔

        Returns:
            list[ChatEvent]: 新事件列表（首次呼叫返回空列表）
        """
        events: list[ChatEvent] = []

        if not self._initialized:
            return self._initialize()

        latest = self._find_latest_file()
        if latest is None:
            return []

        # 檔案輪替偵測：最新檔案與上次不同
        if self._current_file is not None and latest != self._current_file:
            # 先讀完舊檔案的剩餘行
            old_events = self._read_incremental(self._current_file)
            events.extend(old_events)
            # 切換到新檔案，從頭開始讀
            self._current_file = latest
            self._file_offset = 0
            logger.info(
                "Chat log rotated: %s → %s",
                self._current_file, latest,
            )

        # 從上次 offset 讀取增量
        new_events = self._read_incremental(self._current_file or latest)
        events.extend(new_events)

        return events

    def _initialize(self) -> list[ChatEvent]:
        """首次呼叫：定位到最新檔案末尾，不回放歷史。"""
        latest = self._find_latest_file()
        if latest is None:
            logger.warning("No chat log files found in %s", self._chat_dir)
            # 保持 _initialized = False，下次再試
            return []

        self._current_file = latest
        try:
            self._file_offset = latest.stat().st_size
        except OSError:
            self._file_offset = 0
        self._initialized = True
        logger.info(
            "ChatLogTailer initialized: %s (offset=%d)",
            latest.name, self._file_offset,
        )
        return []

    def _read_incremental(self, file_path: Path) -> list[ChatEvent]:
        """從指定偏移量讀取檔案新增內容並解析為事件。"""
        try:
            size = file_path.stat().st_size
        except OSError:
            return []

        if size <= self._file_offset:
            return []

        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                f.seek(self._file_offset)
                new_content = f.read()
                self._file_offset = f.tell()
        except OSError as e:
            logger.error("Failed to read chat log %s: %s", file_path, e)
            return []

        events: list[ChatEvent] = []
        for line in new_content.splitlines():
            line = line.strip()
            if not line:
                continue
            for sub_line in _split_events(line):
                events.append(parse_chat_line(sub_line))

        if events:
            logger.debug(
                "ChatLogTailer: read %d new events from %s",
                len(events), file_path.name,
            )
        return events
