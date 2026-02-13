from __future__ import annotations

from typing import Any

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "status.online": "🟢 Online",
        "status.offline": "🔴 Offline",
        "status.server_info": "📋 Server Info",
        "status.season": "Season",
        "status.weather": "Weather",
        "status.game_time": "Game Time",
        "status.players": "👥 Players",
        "status.online_players": "Online Players",
        "status.ai_status": "🧟 AI Status",
        "status.zombies": "Zombies",
        "status.bandits": "Bandits",
        "status.animals": "Animals",
        "status.system_status": "📊 System Status",
        "status.cpu": "CPU",
        "status.memory": "Memory",
        "status.disk": "Disk",
        "status.network": "Network",
        "status.uptime": "Uptime",
        "status.last_update": "Last Update",
        "chat.joined": "📥 **{name}** joined the server",
        "chat.left": "📤 **{name}** left the server",
        "chat.died": "💀 **{name}** died",
        "chart.title": "Player Count (24h)",
        "chart.ylabel": "Players",
        "season.Spring": "Spring",
        "season.Summer": "Summer",
        "season.Autumn": "Autumn",
        "season.Fall": "Fall",
        "season.Winter": "Winter",
        "weather.Clear": "Clear",
        "weather.Partly Cloudy": "Partly Cloudy",
        "weather.Overcast": "Overcast",
        "weather.Foggy": "Foggy",
        "weather.Light Rain": "Light Rain",
        "weather.Rain": "Rain",
        "weather.Thunder": "Thunder",
        "weather.Light Snow": "Light Snow",
        "weather.Snow": "Snow",
        "weather.Blizzard": "Blizzard",
        "weather.Cloudy": "Cloudy",
        "weather.Storm": "Storm",
        "weather.Fog": "Fog",
    },
    "zh-TW": {
        "status.online": "🟢 線上",
        "status.offline": "🔴 離線",
        "status.server_info": "📋 伺服器資訊",
        "status.season": "季節",
        "status.weather": "天氣",
        "status.game_time": "遊戲時間",
        "status.players": "👥 玩家人數",
        "status.online_players": "線上玩家",
        "status.ai_status": "🧟 AI 狀態",
        "status.zombies": "殭屍",
        "status.bandits": "盜賊",
        "status.animals": "動物",
        "status.system_status": "📊 系統狀態",
        "status.cpu": "處理器",
        "status.memory": "記憶體",
        "status.disk": "磁碟",
        "status.network": "網路",
        "status.uptime": "已運行",
        "status.last_update": "最後更新",
        "chat.joined": "📥 **{name}** 加入了伺服器",
        "chat.left": "📤 **{name}** 離開了伺服器",
        "chat.died": "💀 **{name}** 死亡了",
        "chart.title": "玩家人數（24 小時）",
        "chart.ylabel": "人數",
        "season.Spring": "春天",
        "season.Summer": "夏天",
        "season.Autumn": "秋天",
        "season.Fall": "秋天",
        "season.Winter": "冬天",
        "weather.Clear": "晴朗",
        "weather.Partly Cloudy": "多雲時晴",
        "weather.Overcast": "陰天",
        "weather.Foggy": "大霧",
        "weather.Light Rain": "小雨",
        "weather.Rain": "下雨",
        "weather.Thunder": "雷雨",
        "weather.Light Snow": "小雪",
        "weather.Snow": "下雪",
        "weather.Blizzard": "暴風雪",
        "weather.Cloudy": "多雲",
        "weather.Storm": "暴風雨",
        "weather.Fog": "霧",
    },
}

_current_locale: str = "en"


def set_locale(locale: str) -> None:
    global _current_locale
    if locale not in _STRINGS:
        raise ValueError(f"Unsupported locale: {locale}. Available: {list(_STRINGS)}")
    _current_locale = locale


def t(key: str, **kwargs: Any) -> str:
    table = _STRINGS.get(_current_locale, _STRINGS["en"])
    text = table.get(key) or _STRINGS["en"].get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text
