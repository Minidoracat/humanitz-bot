from __future__ import annotations

import enum
import logging
import re
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


# 編譯正則表達式 patterns
_CHAT_RE = re.compile(r"^<PN>(.+?):</>(.+)$")
# 管理員玩家聊天: <SP>[Admin]</><PN>PlayerName:</>Message
_ADMIN_CHAT_RE = re.compile(r"^<SP>\[Admin\]</><PN>(.+?):</>(.+)$")
_JOIN_RE = re.compile(r"^Player Joined \(<PN>(.+?)</>\)$")
_LEFT_RE = re.compile(r"^Player Left \(<PN>(.+?)</>\)$")
_DIED_RE = re.compile(r"^Player died \(<PN>(.+?)</>\)$")
_ADMIN_RE = re.compile(r"^<SP>Admin: (.+)</>$")


def parse_chat_line(line: str) -> ChatEvent:
    """解析單行 fetchchat 輸出

    Args:
        line: fetchchat 輸出的單行文字

    Returns:
        ChatEvent: 解析後的事件物件

    Examples:
        >>> parse_chat_line("<PN>kevin052926:</>嗨嗨")
        ChatEvent(event_type=ChatEventType.PLAYER_CHAT, player_name='kevin052926', message='嗨嗨', raw_line='...')

        >>> parse_chat_line("Player Joined (<PN>OG83</>)")
        ChatEvent(event_type=ChatEventType.PLAYER_JOINED, player_name='OG83', message='', raw_line='...')
    """
    # 管理員玩家聊天: <SP>[Admin]</><PN>PlayerName:</>MessageText
    # 必須在普通玩家聊天之前檢查，因為普通 regex 無法匹配此格式
    m = _ADMIN_CHAT_RE.match(line)
    if m:
        return ChatEvent(
            event_type=ChatEventType.PLAYER_CHAT,
            player_name=m.group(1),
            message=m.group(2),
            raw_line=line,
        )

    # 普通玩家聊天: <PN>PlayerName:</>MessageText
    m = _CHAT_RE.match(line)
    if m:
        return ChatEvent(
            event_type=ChatEventType.PLAYER_CHAT,
            player_name=m.group(1),
            message=m.group(2),
            raw_line=line,
        )
    # 玩家加入: Player Joined (<PN>PlayerName</>)
    m = _JOIN_RE.match(line)
    if m:
        return ChatEvent(
            event_type=ChatEventType.PLAYER_JOINED,
            player_name=m.group(1),
            message="",
            raw_line=line,
        )

    # 玩家離開: Player Left (<PN>PlayerName</>)
    m = _LEFT_RE.match(line)
    if m:
        return ChatEvent(
            event_type=ChatEventType.PLAYER_LEFT,
            player_name=m.group(1),
            message="",
            raw_line=line,
        )

    # 玩家死亡: Player died (<PN>PlayerName</>)
    m = _DIED_RE.match(line)
    if m:
        return ChatEvent(
            event_type=ChatEventType.PLAYER_DIED,
            player_name=m.group(1),
            message="",
            raw_line=line,
        )

    # 管理員訊息: <SP>Admin: MessageText</>
    m = _ADMIN_RE.match(line)
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

        # 解析新行
        new_events = [parse_chat_line(line) for line in new_lines]
        logger.debug("Parsed %d new events", len(new_events))

        return new_events
