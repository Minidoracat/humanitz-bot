"""伺服器狀態 Cog — 自動更新 Discord Embed 顯示伺服器即時資訊。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
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
from humanitz_bot.utils.i18n import t
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
_STATE_FILE = Path("data/status_state.json")


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
            history_hours=settings.chart_history_hours,
        )

        self.status_channel_id: int = settings.status_channel_id
        self.status_message_id: int | None = settings.status_message_id
        self._update_interval: int = settings.status_update_interval
        self._status_message: discord.Message | None = None
        self._last_result: FetchAllResult | None = None
        self._load_state()

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
        now = datetime.now()

        if result.online and result.server_info:
            info = result.server_info
            embed = discord.Embed(
                title=info.name or "HumanitZ Server",
                description=t("status.online"),
                color=_COLOR_ONLINE,
            )

            season_emoji = get_season_emoji(info.season)
            weather_emoji = get_weather_emoji(info.weather)
            season_name = t(f"season.{info.season}") if info.season else "?"
            weather_name = t(f"weather.{info.weather}") if info.weather else "?"
            embed.add_field(
                name=t("status.server_info"),
                value=(
                    f"🗓️ {t('status.season')}: {season_emoji} {season_name} | "
                    f"🌤️ {t('status.weather')}: {weather_emoji} {weather_name}\n"
                    f"🕐 {t('status.game_time')}: {info.game_time} | 🎯 FPS: {info.fps}"
                ),
                inline=False,
            )

            embed.add_field(
                name=t("status.players"),
                value=f"**{info.player_count}** / {info.max_players}",
                inline=False,
            )

            if info.player_names:
                left, right = self._format_player_columns(
                    info.player_names, online_times, now
                )
                if left:
                    embed.add_field(
                        name=t("status.online_players"), value=left, inline=True
                    )
                    embed.add_field(name="\u200b", value=right or "\u200b", inline=True)

            embed.add_field(
                name=t("status.ai_status"),
                value=(
                    f"{t('status.zombies')}: {info.zombies} | "
                    f"{t('status.bandits')}: {info.humans} | "
                    f"{t('status.animals')}: {info.animals}"
                ),
                inline=False,
            )
        else:
            embed = discord.Embed(
                title="HumanitZ Server",
                description=t("status.offline"),
                color=_COLOR_OFFLINE,
            )

        embed.add_field(
            name=t("status.system_status"),
            value=self._format_system_stats(stats),
            inline=False,
        )

        embed.set_image(url="attachment://player_chart.png")
        embed.set_footer(
            text=f"{t('status.last_update')}: {now.strftime('%m/%d/%Y, %H:%M:%S')}"
        )

        return embed

    @staticmethod
    def _format_player_columns(
        names: list[str],
        online_times: dict[str, datetime],
        now: datetime,
    ) -> tuple[str, str]:
        """Return (left_column, right_column) strings for inline embed fields."""
        entries: list[str] = []
        for name in names:
            connected_at = online_times.get(name)
            if connected_at:
                duration = format_player_duration(now - connected_at)
            else:
                duration = "?"
            entries.append(f"`{name}` ({duration})")

        mid = (len(entries) + 1) // 2
        left = "\n".join(entries[:mid])
        right = "\n".join(entries[mid:])

        if len(left) > _EMBED_FIELD_LIMIT:
            left = left[: _EMBED_FIELD_LIMIT - 20] + "\n... and more"
        if len(right) > _EMBED_FIELD_LIMIT:
            right = right[: _EMBED_FIELD_LIMIT - 20] + "\n... and more"
        return left, right

    @staticmethod
    def _format_system_stats(stats: SystemStats) -> str:
        cpu_bar = make_progress_bar(stats.cpu_percent)
        mem_bar = make_progress_bar(stats.memory_percent)
        disk_bar = make_progress_bar(stats.disk_percent)
        uptime = format_duration(timedelta(seconds=stats.uptime_seconds))
        net_recv = format_bytes(stats.net_recv_per_sec)
        net_sent = format_bytes(stats.net_sent_per_sec)

        return (
            f"💻 {t('status.cpu')}: {cpu_bar} {stats.cpu_percent}%\n"
            f"🧠 {t('status.memory')}: {mem_bar} {stats.memory_percent}% "
            f"({stats.memory_used:.2f}/{stats.memory_total:.2f} GB)\n"
            f"💾 {t('status.disk')}: {disk_bar} {stats.disk_percent}% "
            f"({stats.disk_used:.2f}/{stats.disk_total:.2f} GB)\n"
            f"🌐 {t('status.network')}: ↓{net_recv} ↑{net_sent}\n"
            f"⏰ {t('status.uptime')}: {uptime}"
        )

    def _load_state(self) -> None:
        """從 data/status_state.json 載入持久化的 message ID。"""
        if self.status_message_id is not None:
            logger.debug("Using STATUS_MESSAGE_ID from .env, skipping state file")
            return

        try:
            if _STATE_FILE.exists():
                data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
                saved_channel = data.get("channel_id")
                saved_msg = data.get("message_id")
                if saved_channel == self.status_channel_id and saved_msg:
                    self.status_message_id = int(saved_msg)
                    logger.info(
                        "Loaded saved status message ID: %d", self.status_message_id
                    )
        except Exception:
            logger.warning("Failed to load status state, will create new message")

    def _save_state(self, message_id: int) -> None:
        """將 message ID 寫入 data/status_state.json 以持久化。"""
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(
                json.dumps(
                    {"channel_id": self.status_channel_id, "message_id": message_id}
                ),
                encoding="utf-8",
            )
            logger.debug("Saved status message ID: %d", message_id)
        except Exception:
            logger.warning("Failed to save status state")

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
        self._save_state(self._status_message.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerStatusCog(bot))
