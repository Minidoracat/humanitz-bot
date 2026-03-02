from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import discord
from discord.ext import commands, tasks

from humanitz_bot.services.database import Database
from humanitz_bot.services.rcon_service import RconService
from humanitz_bot.utils.chat_parser import (
    ChatDiffer,
    ChatEvent,
    ChatEventType,
    ChatLogTailer,
)
from humanitz_bot.utils.i18n import t

_SESSION_EVENT_TYPES = frozenset(
    {
        ChatEventType.PLAYER_JOINED,
        ChatEventType.PLAYER_LEFT,
        ChatEventType.PLAYER_DIED,
    }
)

logger = logging.getLogger("humanitz_bot.cogs.chat_bridge")

_MENTION_RE = re.compile(r"@(everyone|here|&?\d+)")

_COMMAND_PREFIX = "!"


def _sanitize_for_discord(text: str) -> str:
    """消毒文字以防止 Discord mention 攻擊（@everyone / @here / <@...>）。"""
    text = discord.utils.escape_mentions(text)
    text = text.replace("<@", "<\u200b@")
    return text


class ChatBridgeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        settings = bot.settings  # type: ignore[attr-defined]

        self.chat_channel_id: int = settings.chat_channel_id
        self.chat_poll_interval: int = settings.chat_poll_interval

        # 模式選擇：HZLOGS_PATH 有設定且 Chat 子目錄存在 → 檔案模式，否則 → RCON 模式
        self._file_mode: bool = False
        self._tailer: ChatLogTailer | None = None
        self._differ: ChatDiffer | None = None

        if settings.hzlogs_path:
            chat_dir = Path(settings.hzlogs_path) / "Chat"
            if chat_dir.is_dir():
                self._tailer = ChatLogTailer(chat_dir)
                self._file_mode = True
                logger.info("Chat bridge: file mode (reading %s)", chat_dir)
            else:
                logger.warning(
                    "HZLOGS_PATH set but Chat/ subdirectory not found: %s — falling back to RCON",
                    chat_dir,
                )

        if not self._file_mode:
            self._differ = ChatDiffer()
            logger.info("Chat bridge: RCON mode (fetchchat)")

        # RCON 連線 — 檔案模式也需要（Discord→遊戲轉發用）
        self._rcon = RconService(
            settings.rcon_host, settings.rcon_port, settings.rcon_password
        )

    def _get_db(self) -> Database | None:
        status_cog = self.bot.get_cog("ServerStatusCog")
        if status_cog is not None:
            return getattr(status_cog, "db", None)
        return None

    def _get_rcon(self) -> RconService:
        """取得聊天橋接專用的 RCON 連線。"""
        return self._rcon

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.poll_chat.is_running():
            self.poll_chat.change_interval(seconds=self.chat_poll_interval)
            self.poll_chat.start()
            mode = "file" if self._file_mode else "RCON"
            logger.info(
                "Chat bridge poll loop started (mode=%s, interval=%ds)",
                mode,
                self.chat_poll_interval,
            )

    async def cog_unload(self) -> None:
        self.poll_chat.cancel()
        await self._rcon.close()

    @tasks.loop(seconds=10)
    async def poll_chat(self) -> None:
        try:
            if self._file_mode:
                await self._poll_file()
            else:
                await self._poll_rcon()
        except Exception:
            logger.exception("Chat poll failed")

    async def _poll_file(self) -> None:
        """檔案模式：從 HZLogs/Chat/ 讀取新事件。"""
        if self._tailer is None:
            return

        new_events = await asyncio.to_thread(self._tailer.get_new_events)

        if not new_events:
            return

        await self._dispatch_events(new_events)

    async def _poll_rcon(self) -> None:
        """RCON 模式：透過 fetchchat 取得新事件。"""
        if self._differ is None:
            return

        rcon = self._get_rcon()
        chat_raw = await rcon.execute("fetchchat")

        if not chat_raw:
            return

        new_events = self._differ.get_new_events(chat_raw)

        if not new_events:
            return

        await self._dispatch_events(new_events)

    async def _dispatch_events(self, new_events: list[ChatEvent]) -> None:
        """處理事件分發（記錄、指令路由、發送到 Discord）。"""
        channel = self.bot.get_channel(self.chat_channel_id)
        if not isinstance(channel, discord.TextChannel):
            logger.error(
                "Chat channel not found or wrong type: %d", self.chat_channel_id
            )
            return

        db = self._get_db()
        for event in new_events:
            if db and event.event_type != ChatEventType.UNKNOWN:
                await asyncio.to_thread(self._log_event, db, event)

            # 偵測遊戲內指令（! 前綴）
            if (
                event.event_type == ChatEventType.PLAYER_CHAT
                and event.message.startswith(_COMMAND_PREFIX)
            ):
                await self._route_game_command(
                    event.player_name, event.message, channel, "game"
                )
                continue

            msg = self._format_event(event)
            if msg:
                await channel.send(msg)

    @poll_chat.before_loop
    async def before_poll_chat(self) -> None:
        await self.bot.wait_until_ready()

    @staticmethod
    def _log_event(db: Database, event: ChatEvent) -> None:
        db.add_chat_event(
            event_type=event.event_type.value,
            player_name=event.player_name,
            message=event.message,
        )
        if event.event_type in _SESSION_EVENT_TYPES:
            db.add_player_session_event(
                player_name=event.player_name,
                event_type=event.event_type.value,
            )

    @staticmethod
    def _format_event(event: ChatEvent) -> str | None:
        # Skip admin messages to prevent echo loop:
        # Discord → RCON admin → fetchchat/file → back to Discord
        if event.event_type == ChatEventType.ADMIN_MESSAGE:
            return None

        if event.event_type == ChatEventType.PLAYER_CHAT:
            return f"**{_sanitize_for_discord(event.player_name)}**: {_sanitize_for_discord(event.message)}"

        if event.event_type == ChatEventType.PLAYER_JOINED:
            return t("chat.joined", name=_sanitize_for_discord(event.player_name))

        if event.event_type == ChatEventType.PLAYER_LEFT:
            return t("chat.left", name=_sanitize_for_discord(event.player_name))

        if event.event_type == ChatEventType.PLAYER_DIED:
            return t("chat.died", name=_sanitize_for_discord(event.player_name))

        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if message.channel.id != self.chat_channel_id:
            return

        # Strip @everyone/@here mentions to prevent abuse
        content = _MENTION_RE.sub("@\u200b\\1", message.content)

        if not content.strip():
            return

        # 偵測 Discord 頻道中的指令
        if content.strip().startswith(_COMMAND_PREFIX):
            channel = self.bot.get_channel(self.chat_channel_id)
            if isinstance(channel, discord.TextChannel):
                await self._route_game_command(
                    message.author.display_name,
                    content.strip(),
                    channel,
                    "discord",
                    message=message,
                )
            return

        max_len = 200
        if len(content) > max_len:
            content = content[:max_len] + "..."

        admin_msg = f"[Discord] {message.author.display_name}: {content}"

        rcon = self._get_rcon()
        response = await rcon.execute(f"admin {admin_msg}", read_timeout=1.5)

        if response and "Message sent!" in response:
            logger.debug(
                "Forwarded Discord message from %s", message.author.display_name
            )
        else:
            logger.warning("Failed to forward message: %s", response)

    async def _route_game_command(
        self,
        player_name: str,
        command_text: str,
        channel: discord.TextChannel,
        source: str,
        message: discord.Message | None = None,
    ) -> None:
        """將指令路由到 AdminCommandsCog 或 GameCommandsCog。"""
        # 先嘗試管理員指令
        admin_cog = self.bot.get_cog("AdminCommandsCog")
        if admin_cog is not None:
            try:
                handled = await admin_cog.handle_command(  # type: ignore[attr-defined]
                    player_name=player_name,
                    command_text=command_text,
                    channel=channel,
                    source=source,
                    message=message,
                )
                if handled:
                    return
            except Exception:
                logger.exception(
                    "Admin command failed (not falling through): %s", command_text
                )
                return  # 管理指令失敗時終止路由，不再 fallthrough 到一般指令

        # 落入一般遊戲指令
        game_cmd_cog = self.bot.get_cog("GameCommandsCog")
        if game_cmd_cog is None:
            logger.debug(
                "GameCommandsCog not loaded, ignoring command: %s", command_text
            )
            return

        try:
            await game_cmd_cog.handle_command(  # type: ignore[attr-defined]
                player_name=player_name,
                command_text=command_text,
                channel=channel,
                source=source,
                message=message,
            )
        except Exception:
            logger.exception("Failed to handle game command: %s", command_text)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChatBridgeCog(bot))
