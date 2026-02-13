from __future__ import annotations

import logging
import re

import discord
from discord.ext import commands, tasks

from humanitz_bot.services.rcon_service import RconService
from humanitz_bot.utils.chat_parser import ChatDiffer, ChatEvent, ChatEventType
from humanitz_bot.utils.i18n import t

logger = logging.getLogger("humanitz_bot.cogs.chat_bridge")

_MENTION_RE = re.compile(r"@(everyone|here|&?\d+)")


class ChatBridgeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        settings = bot.settings  # type: ignore[attr-defined]

        self.chat_channel_id: int = settings.chat_channel_id
        self.chat_poll_interval: int = settings.chat_poll_interval
        self.chat_differ = ChatDiffer()

        self._own_rcon: RconService | None = None

    def _get_rcon(self) -> RconService:
        status_cog = self.bot.get_cog("ServerStatusCog")
        if status_cog is not None:
            return status_cog.rcon  # type: ignore[attr-defined]

        if self._own_rcon is None:
            settings = self.bot.settings  # type: ignore[attr-defined]
            self._own_rcon = RconService(
                settings.rcon_host, settings.rcon_port, settings.rcon_password
            )
        return self._own_rcon

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
        if self._own_rcon:
            await self._own_rcon.close()

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

            for event in new_events:
                msg = self._format_event(event)
                if msg:
                    await channel.send(msg)

        except Exception:
            logger.exception("Chat poll failed")

    @poll_chat.before_loop
    async def before_poll_chat(self) -> None:
        await self.bot.wait_until_ready()

    @staticmethod
    def _format_event(event: ChatEvent) -> str | None:
        # Skip admin messages to prevent echo loop:
        # Discord → RCON admin → fetchchat → back to Discord
        if event.event_type == ChatEventType.ADMIN_MESSAGE:
            return None

        if event.event_type == ChatEventType.PLAYER_CHAT:
            return f"**{event.player_name}**: {event.message}"

        if event.event_type == ChatEventType.PLAYER_JOINED:
            return t("chat.joined", name=event.player_name)

        if event.event_type == ChatEventType.PLAYER_LEFT:
            return t("chat.left", name=event.player_name)

        if event.event_type == ChatEventType.PLAYER_DIED:
            return t("chat.died", name=event.player_name)

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

        max_len = 200
        if len(content) > max_len:
            content = content[:max_len] + "..."

        admin_msg = f"[Discord] {message.author.display_name}: {content}"

        rcon = self._get_rcon()
        response = await rcon.execute(f"admin {admin_msg}")

        if response and "Message sent!" in response:
            logger.debug(
                "Forwarded Discord message from %s", message.author.display_name
            )
        else:
            logger.warning("Failed to forward message: %s", response)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChatBridgeCog(bot))
