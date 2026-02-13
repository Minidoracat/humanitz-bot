"""HumanitZ Discord Bot 進入點"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from humanitz_bot.config import Settings
from humanitz_bot.bot import create_bot


def setup_logging(level: str = "INFO") -> None:
    """
    設定 logging 格式與等級

    Args:
        level: Log 等級（DEBUG, INFO, WARNING, ERROR, CRITICAL）
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


async def shutdown(bot, logger):
    """優雅關閉 bot"""
    logger.info("Shutdown signal received, closing bot...")
    await bot.close()


async def main() -> None:
    """主程式進入點"""
    # 載入設定
    try:
        settings = Settings.from_env()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    # 設定 logging
    setup_logging(settings.log_level)
    logger = logging.getLogger("humanitz_bot")
    logger.info("Starting HumanitZ Discord Bot...")

    # 建立 bot
    bot = await create_bot(settings)

    # Signal handling 以便優雅關閉
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(bot, logger)))

    # 啟動 bot
    try:
        await bot.start(settings.discord_token)
    except Exception as e:
        logger.error("Bot crashed: %s", e)
    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
