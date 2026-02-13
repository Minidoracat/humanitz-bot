"""HumanitZ Discord Bot 進入點"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from humanitz_bot.config import Settings
from humanitz_bot.bot import create_bot
from humanitz_bot.utils.i18n import set_locale

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO", retention_days: int = 7) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)

    file_handler = TimedRotatingFileHandler(
        filename=_LOG_DIR / "bot.log",
        when="midnight",
        interval=1,
        backupCount=retention_days,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(log_level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


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
    setup_logging(settings.log_level, settings.log_retention_days)
    set_locale(settings.locale)
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
