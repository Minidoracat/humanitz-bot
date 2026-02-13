"""伺服器狀態 Cog — 自動更新 Discord Embed 顯示伺服器即時資訊。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord.ext import commands, tasks

from humanitz_bot.services.chart_service import ChartService
from humanitz_bot.services.player_tracker import PlayerTracker
from humanitz_bot.services.player_tracker import (
    format_duration as format_player_duration,
)
from humanitz_bot.services.rcon_service import FetchAllResult, RconService
from humanitz_bot.services.system_stats import SystemStats, get_system_stats
from humanitz_bot.utils.formatters import (
    format_bytes,
    format_duration,
    get_season_emoji,
    get_weather_emoji,
    make_progress_bar,
)

logger = logging.getLogger("humanitz_bot.cogs.server_status")

_COLOR_ONLINE = 0x2ECC71
_COLOR_OFFLINE = 0xE74C3C
_EMBED_FIELD_LIMIT = 1024


class ServerStatusCog(commands.Cog):
    """定時抓取 RCON / 系統資料，編輯固定 Discord 訊息顯示伺服器狀態。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        settings = bot.settings  # type: ignore[attr-defined]

        self.rcon = RconService(
            settings.rcon_host, settings.rcon_port, settings.rcon_password
        )
        self.player_tracker = PlayerTracker(settings.player_log_path)
        self.chart_service = ChartService(
            data_dir=str(Path("data")),
            tmp_dir=str(Path("tmp")),
        )

        self.status_channel_id: int = settings.status_channel_id
        self.status_message_id: int | None = settings.status_message_id
        self._update_interval: int = settings.status_update_interval
        self._status_message: discord.Message | None = None
        self._last_result: FetchAllResult | None = None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.update_status.is_running():
            self.update_status.change_interval(seconds=self._update_interval)
            self.update_status.start()
            logger.info(
                "Status update loop started (interval=%ds)", self._update_interval
            )

    async def cog_unload(self) -> None:
        self.update_status.cancel()
        await self.rcon.close()

    @tasks.loop(seconds=30)
    async def update_status(self) -> None:
        try:
            result = await self.rcon.fetch_all()
            self._last_result = result

            online_times: dict[str, datetime] = {}
            if result.server_info and result.server_info.player_names:
                online_times = await asyncio.to_thread(
                    self.player_tracker.get_online_times,
                    result.server_info.player_names,
                )

            stats = await asyncio.to_thread(get_system_stats)

            player_count = result.server_info.player_count if result.server_info else 0
            await asyncio.to_thread(self.chart_service.add_data_point, player_count)
            chart_path = await asyncio.to_thread(self.chart_service.generate_chart)

            embed = self._build_embed(result, online_times, stats)

            await self._update_message(embed, chart_path)

            logger.debug("Status embed updated")
        except Exception:
            logger.exception("Status update failed")

    @update_status.before_loop
    async def before_update_status(self) -> None:
        await self.bot.wait_until_ready()

    def _build_embed(
        self,
        result: FetchAllResult,
        online_times: dict[str, datetime],
        stats: SystemStats,
    ) -> discord.Embed:
        now = datetime.now(tz=timezone.utc)

        if result.online and result.server_info:
            info = result.server_info
            embed = discord.Embed(
                title=info.name or "HumanitZ Server",
                description="🟢 Online",
                color=_COLOR_ONLINE,
            )

            season_emoji = get_season_emoji(info.season)
            weather_emoji = get_weather_emoji(info.weather)
            embed.add_field(
                name="📋 Server Info",
                value=(
                    f"🗓️ Season: {season_emoji} {info.season} | "
                    f"🌤️ Weather: {weather_emoji} {info.weather}\n"
                    f"🕐 Game Time: {info.game_time} | 🎯 FPS: {info.fps}"
                ),
                inline=False,
            )

            embed.add_field(
                name="👥 Players",
                value=f"**{info.player_count}** / {info.max_players}",
                inline=False,
            )

            if info.player_names:
                player_lines = self._format_player_list(
                    info.player_names, online_times, now
                )
                if player_lines:
                    embed.add_field(
                        name="Online Players", value=player_lines, inline=False
                    )

            embed.add_field(
                name="🧟 AI Status",
                value=f"Zombies: {info.zombies} | Bandits: {info.humans} | Animals: {info.animals}",
                inline=False,
            )
        else:
            embed = discord.Embed(
                title="HumanitZ Server",
                description="🔴 Offline",
                color=_COLOR_OFFLINE,
            )

        embed.add_field(
            name="📊 System Status",
            value=self._format_system_stats(stats),
            inline=False,
        )

        embed.set_image(url="attachment://player_chart.png")
        embed.set_footer(text=f"Last Update: {now.strftime('%m/%d/%Y, %H:%M:%S')}")

        return embed

    @staticmethod
    def _format_player_list(
        names: list[str],
        online_times: dict[str, datetime],
        now: datetime,
    ) -> str:
        entries: list[str] = []
        for name in names:
            connected_at = online_times.get(name)
            if connected_at:
                if connected_at.tzinfo is None:
                    connected_at = connected_at.replace(tzinfo=timezone.utc)
                duration = format_player_duration(now - connected_at)
            else:
                duration = "?"
            entries.append(f"`{name}` ({duration})")

        lines: list[str] = []
        for i in range(0, len(entries), 2):
            pair = entries[i : i + 2]
            lines.append("    ".join(pair))

        text = "\n".join(lines)
        if len(text) > _EMBED_FIELD_LIMIT:
            text = text[: _EMBED_FIELD_LIMIT - 20] + "\n... and more"
        return text

    @staticmethod
    def _format_system_stats(stats: SystemStats) -> str:
        cpu_bar = make_progress_bar(stats.cpu_percent)
        mem_bar = make_progress_bar(stats.memory_percent)
        disk_bar = make_progress_bar(stats.disk_percent)
        uptime = format_duration(timedelta(seconds=stats.uptime_seconds))
        net_recv = format_bytes(stats.net_recv_per_sec)
        net_sent = format_bytes(stats.net_sent_per_sec)

        return (
            f"💻 CPU: {cpu_bar} {stats.cpu_percent}%\n"
            f"🧠 Memory: {mem_bar} {stats.memory_percent}% "
            f"({stats.memory_used:.2f}/{stats.memory_total:.2f} GB)\n"
            f"💾 Disk: {disk_bar} {stats.disk_percent}% "
            f"({stats.disk_used:.2f}/{stats.disk_total:.2f} GB)\n"
            f"🌐 Network: ↓{net_recv} ↑{net_sent}\n"
            f"⏰ Uptime: {uptime}"
        )

    async def _update_message(
        self, embed: discord.Embed, chart_path: str | None
    ) -> None:
        raw_channel = self.bot.get_channel(self.status_channel_id)
        if not isinstance(raw_channel, discord.TextChannel):
            logger.error(
                "Status channel not found or not a text channel: %d",
                self.status_channel_id,
            )
            return
        channel: discord.TextChannel = raw_channel

        file = (
            discord.File(chart_path, filename="player_chart.png")
            if chart_path
            else None
        )

        if self._status_message is not None:
            try:
                if file:
                    await self._status_message.edit(embed=embed, attachments=[file])
                else:
                    await self._status_message.edit(embed=embed)
                return
            except discord.NotFound:
                self._status_message = None

        if self.status_message_id:
            try:
                self._status_message = await channel.fetch_message(
                    self.status_message_id
                )
                if file:
                    await self._status_message.edit(embed=embed, attachments=[file])
                else:
                    await self._status_message.edit(embed=embed)
                return
            except discord.NotFound:
                logger.warning(
                    "Status message %d not found, creating new", self.status_message_id
                )

        if file:
            self._status_message = await channel.send(embed=embed, file=file)
        else:
            self._status_message = await channel.send(embed=embed)
        logger.info("Created new status message: %d", self._status_message.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerStatusCog(bot))
