"""Discord Embed 格式化工具 — 進度條、時間格式、流量單位。"""

from __future__ import annotations

import logging
from datetime import timedelta

logger = logging.getLogger("humanitz_bot.utils.formatters")


def make_progress_bar(percent: float, length: int = 10) -> str:
    """產生文字進度條。

    Example:
        >>> make_progress_bar(63.8)
        '▰▰▰▰▰▰▱▱▱▱'
    """
    filled = round(percent / 100 * length)
    filled = max(0, min(filled, length))
    return "▰" * filled + "▱" * (length - filled)


def format_duration(td: timedelta) -> str:
    """將 timedelta 格式化為人類可讀時長（支援天數）。

    Examples:
        >>> format_duration(timedelta(days=4, hours=21, minutes=10))
        '4d21h10m'
        >>> format_duration(timedelta(hours=1, minutes=18))
        '1h18m'
        >>> format_duration(timedelta(minutes=38))
        '38m'
        >>> format_duration(timedelta(seconds=30))
        '<1m'
    """
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        total_seconds = 0

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    if days > 0:
        return f"{days}d{hours}h{minutes}m"
    if hours > 0:
        return f"{hours}h{minutes}m"
    if minutes > 0:
        return f"{minutes}m"
    return "<1m"


def format_bytes(n: float) -> str:
    """將每秒位元組數格式化為人類可讀單位。

    Examples:
        >>> format_bytes(8600)
        '8.4 KB/s'
        >>> format_bytes(1048576)
        '1.0 MB/s'
        >>> format_bytes(256)
        '256 B/s'
    """
    if n < 1024:
        return f"{n:.0f} B/s"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB/s"
    else:
        return f"{n / (1024 * 1024):.1f} MB/s"


SEASON_EMOJI: dict[str, str] = {
    "Spring": "🌸",
    "Summer": "☀️",
    "Autumn": "🍂",
    "Fall": "🍂",
    "Winter": "❄️",
}

WEATHER_EMOJI: dict[str, str] = {
    "Clear": "☀️",
    "Overcast": "🌥️",
    "Cloudy": "☁️",
    "Rain": "🌧️",
    "Storm": "⛈️",
    "Snow": "🌨️",
    "Fog": "🌫️",
}


def get_season_emoji(season: str) -> str:
    """取得季節對應的 emoji。"""
    return SEASON_EMOJI.get(season, "🗓️")


def get_weather_emoji(weather: str) -> str:
    """取得天氣對應的 emoji。"""
    return WEATHER_EMOJI.get(weather, "🌤️")
