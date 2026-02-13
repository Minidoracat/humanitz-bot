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
    # 玩家聊天: <PN>PlayerName:</>MessageText
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

    def __init__(self):
        self._last_lines: list[str] = []
        self._initialized: bool = False

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

        # 尋找重疊點：從 _last_lines 的末尾開始往前找
        overlap_index = -1
        for i in range(len(self._last_lines) - 1, -1, -1):
            try:
                overlap_index = current_lines.index(self._last_lines[i])
                logger.debug(
                    "Found overlap at index %d: %s", overlap_index, self._last_lines[i]
                )
                break
            except ValueError:
                continue

        # 計算新行
        if overlap_index >= 0:
            # 找到重疊：新行 = overlap_index 之後的所有行
            new_lines = current_lines[overlap_index + 1 :]
        else:
            # 沒有重疊：所有 current_lines 都是新的
            logger.debug(
                "No overlap found, treating all %d lines as new", len(current_lines)
            )
            new_lines = current_lines

        # 更新快照
        self._last_lines = current_lines

        # 解析新行
        new_events = [parse_chat_line(line) for line in new_lines]
        logger.debug("Parsed %d new events", len(new_events))

        return new_events
