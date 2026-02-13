"""Discord Bot 初始化與 Cog 管理"""

from __future__ import annotations

import logging
import discord
from discord.ext import commands
from humanitz_bot.config import Settings

logger = logging.getLogger("humanitz_bot.bot")


async def create_bot(settings: Settings) -> commands.Bot:
    """
    建立並設定 Discord bot

    Args:
        settings: Bot 設定

    Returns:
        已設定的 Bot 實例
    """
    # 設定所需的 intents
    intents = discord.Intents.default()
    intents.message_content = True  # 讀取訊息內容所需
    intents.messages = True
    intents.guilds = True

    # 建立 bot
    bot = commands.Bot(
        command_prefix="!",  # 雖然不使用前綴指令，但 discord.py 需要
        intents=intents,
    )

    # 將 settings 儲存到 bot 供 Cogs 使用
    bot.settings = settings  # type: ignore[attr-defined]

    @bot.event
    async def on_ready():
        """Bot 就緒時的回呼"""
        logger.info(
            "Bot is ready: %s (ID: %s)", bot.user, bot.user.id if bot.user else "?"
        )
        logger.info("Connected to %d guild(s)", len(bot.guilds))

    # 載入 cogs（使用 try/except 因為 cogs 可能尚未實作）
    cogs = [
        "humanitz_bot.cogs.server_status",
        "humanitz_bot.cogs.chat_bridge",
    ]

    for cog in cogs:
        try:
            await bot.load_extension(cog)
            logger.info("Loaded cog: %s", cog)
        except Exception as e:
            logger.warning("Failed to load cog %s: %s", cog, e)

    logger.info("Bot initialization complete")

    return bot
