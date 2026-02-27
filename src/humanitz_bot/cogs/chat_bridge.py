from __future__ import annotations

import asyncio

import logging
import re

import discord
from discord.ext import commands, tasks

from humanitz_bot.services.database import Database
from humanitz_bot.services.rcon_service import RconService
from humanitz_bot.utils.chat_parser import ChatDiffer, ChatEvent, ChatEventType
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
        self.chat_differ = ChatDiffer()

        # 獨立 RCON 連線 — fetchchat + Discord→遊戲轉發，不與 status loop 爭搶鎖
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
            logger.info(
                "Chat bridge poll loop started (interval=%ds)", self.chat_poll_interval
            )

    async def cog_unload(self) -> None:
        self.poll_chat.cancel()
        await self._rcon.close()

    @tasks.loop(seconds=10)
    async def poll_chat(self) -> None:
        try:
            rcon = self._get_rcon()
            chat_raw = await rcon.execute("fetchchat")

            if not chat_raw:
                return

            new_events = self.chat_differ.get_new_events(chat_raw)

            if not new_events:
                return

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
        except Exception:
            logger.exception("Chat poll failed")

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
        # Discord → RCON admin → fetchchat → back to Discord
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
    ) -> None:
        """將遊戲指令路由到 GameCommandsCog。"""
        game_cmd_cog = self.bot.get_cog("GameCommandsCog")
        if game_cmd_cog is None:
            logger.debug("GameCommandsCog not loaded, ignoring command: %s", command_text)
            return

        try:
            await game_cmd_cog.handle_command(  # type: ignore[attr-defined]
                player_name=player_name,
                command_text=command_text,
                channel=channel,
                source=source,
            )
        except Exception:
            logger.exception("Failed to handle game command: %s", command_text)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChatBridgeCog(bot))
