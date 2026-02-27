"""遊戲指令處理 Cog — 處理遊戲內 ! 指令、查詢存檔資料、雙向回應。"""

from __future__ import annotations

import asyncio
import logging
import time
import re
from datetime import datetime

import discord
from discord.ext import commands

from humanitz_bot.services.player_identity import PlayerIdentityService
from humanitz_bot.services.rcon_service import RconService
from humanitz_bot.services.save_service import SaveService
from humanitz_bot.utils.i18n import _STRINGS
from humanitz_bot.utils import i18n

logger = logging.getLogger("humanitz_bot.cogs.game_commands")

_COLOR_INFO = 0x3498DB
_COLOR_ERROR = 0xE74C3C
_COMMAND_COOLDOWN_SECONDS = 5

# alias → (command_name, locale)
_COMMAND_ALIASES: dict[str, tuple[str, str]] = {
    "coords": ("coords", "en"),
    "位置": ("coords", "zh-TW"),
    "stats": ("stats", "en"),
    "狀態": ("stats", "zh-TW"),
    "top": ("top", "en"),
    "排行": ("top", "zh-TW"),
    "kills": ("kills", "en"),
    "擊殺": ("kills", "zh-TW"),
    "server": ("server", "en"),
    "伺服器": ("server", "zh-TW"),
    "help": ("help", "en"),
    "幫助": ("help", "zh-TW"),
}

# 擊殺類型 label — 用於動態建構明細（只顯示非零值）
_KILL_TYPE_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "melee": "⚔️Melee", "gun": "🔫Ranged", "blast": "💥Blast",
        "fist": "👊Fist", "vehicle": "🚗Vehicle", "takedown": "🤼Takedown",
    },
    "zh-TW": {
        "melee": "⚔️近戰", "gun": "🔫遠程", "blast": "💥爆裂",
        "fist": "👊徒手", "vehicle": "🚗載具", "takedown": "🤼處決",
    },
}


def _build_kill_detail(player: object, locale: str) -> str:
    """動態建構擊殺明細字串 — 只包含非零的擊殺類型，用 · 分隔。"""
    labels = _KILL_TYPE_LABELS.get(locale, _KILL_TYPE_LABELS["en"])
    types = [
        (labels["melee"], getattr(player, "melee_kills", 0)),
        (labels["gun"], getattr(player, "gun_kills", 0)),
        (labels["blast"], getattr(player, "blast_kills", 0)),
        (labels["fist"], getattr(player, "fist_kills", 0)),
        (labels["vehicle"], getattr(player, "vehicle_kills", 0)),
        (labels["takedown"], getattr(player, "takedown_kills", 0)),
    ]
    parts = [f"{label} {count}" for label, count in types if count > 0]
    return " · ".join(parts)


def _t(key: str, locale: str, **kwargs: object) -> str:
    """取得指定語系的翻譯字串。"""
    table = _STRINGS.get(locale, _STRINGS["en"])
    text = table.get(key) or _STRINGS["en"].get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text


class GameCommandsCog(commands.Cog):
    """處理遊戲內 ! 指令，查詢存檔資料並雙向回應（Discord embed + RCON）。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._cooldowns: dict[str, float] = {}  # player_name → last_command_time

        # 獨立 RCON 連線 — 指令回應不與 status/chat 爭搶鎖
        settings = bot.settings  # type: ignore[attr-defined]
        self._rcon = RconService(
            settings.rcon_host, settings.rcon_port, settings.rcon_password
        )
        self._background_tasks: set[asyncio.Task[None]] = set()

    def _get_save_service(self) -> SaveService | None:
        """從 ServerStatusCog 取得 SaveService 實例。"""
        status_cog = self.bot.get_cog("ServerStatusCog")
        if status_cog is not None:
            return getattr(status_cog, "save_service", None)
        return None

    def _get_identity_service(self) -> PlayerIdentityService | None:
        """從 ServerStatusCog 取得 PlayerIdentityService 實例。"""
        status_cog = self.bot.get_cog("ServerStatusCog")
        if status_cog is not None:
            return getattr(status_cog, "identity_service", None)
        return None

    def _get_rcon(self) -> RconService:
        """取得指令專用的 RCON 連線。"""
        return self._rcon

    def _spawn_background(self, coro: object) -> None:
        """建立背景 task 並自動清理引用，避免 GC 回收。"""
        task = asyncio.create_task(coro)  # type: ignore[arg-type]
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def cog_unload(self) -> None:
        """Cog 卸載時取消背景任務並關閉獨立 RCON 連線。"""
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()
        await self._rcon.close()

    def _check_cooldown(self, player_name: str) -> float:
        """檢查指令冷卻。回傳剩餘冷卻秒數，0 表示可執行。"""
        now = time.time()
        last = self._cooldowns.get(player_name, 0.0)
        remaining = _COMMAND_COOLDOWN_SECONDS - (now - last)
        if remaining > 0:
            return remaining
        self._cooldowns[player_name] = now
        return 0.0

    async def handle_command(
        self,
        player_name: str,
        command_text: str,
        channel: discord.TextChannel,
        source: str = "game",
    ) -> None:
        """主要進入點 — 由 chat_bridge 呼叫處理 ! 指令。

        Args:
            player_name: 發出指令的玩家名稱
            command_text: 完整指令文字（含 ! 前綴）
            channel: Discord 頻道
            source: 來源，"game" 或 "discord"
        """
        # 檢查功能是否啟用
        settings = getattr(self.bot, "settings", None)
        if settings is not None and not getattr(settings, "enable_game_commands", True):
            return

        # 解析指令：去除 ! 前綴，取第一個詞作為指令名
        raw = command_text.lstrip("!").strip()
        if not raw:
            return

        parts = raw.split(maxsplit=1)
        alias = parts[0]

        # 查找別名
        mapping = _COMMAND_ALIASES.get(alias)
        if mapping is None:
            # 未知指令 — 使用 .env 全域語系
            locale = i18n._current_locale
            embed = discord.Embed(
                description=_t("cmd.unknown", locale, command=alias),
                color=_COLOR_ERROR,
            )
            plain = _t("cmd.unknown", locale, command=alias)
            await self._send_response(channel, embed, plain, source)
            return

        cmd_name, locale = mapping

        # 檢查冷卻
        remaining = self._check_cooldown(player_name)
        if remaining > 0:
            embed = discord.Embed(
                description=_t("cmd.cooldown", locale, seconds=int(remaining) + 1),
                color=_COLOR_ERROR,
            )
            plain = _t("cmd.cooldown", locale, seconds=int(remaining) + 1)
            await self._send_response(channel, embed, plain, source)
            return

        # 檢查存檔資料是否過期，過期則背景觸發解析（不阻塞回應）
        save = self._get_save_service()
        if save is not None and save.is_available and not save.is_parsing:
            settings = getattr(self.bot, "settings", None)
            cooldown = getattr(settings, "save_parse_cooldown", 60) if settings else 60
            if save.is_stale(cooldown):
                self._spawn_background(self._trigger_parse(save))

        # 路由到對應的指令處理器
        try:
            if cmd_name == "coords":
                embed, plain = await self._cmd_coords(player_name, locale)
            elif cmd_name == "stats":
                embed, plain = await self._cmd_stats(player_name, locale)
            elif cmd_name == "top":
                embed, plain = await self._cmd_top(locale)
            elif cmd_name == "kills":
                embed, plain = await self._cmd_kills(locale)
            elif cmd_name == "server":
                embed, plain = await self._cmd_server(locale)
            elif cmd_name == "help":
                embed, plain = await self._cmd_help(locale)
            else:
                return

            await self._send_response(channel, embed, plain, source)

        except Exception:
            logger.exception("Command handler failed: %s", cmd_name)

    async def _send_response(
        self,
        channel: discord.TextChannel,
        embed: discord.Embed,
        plain_text: str,
        source: str,
    ) -> None:
        """發送回應到 Discord 頻道，遊戲來源額外透過 RCON 發送。"""
        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error("Failed to send Discord response: %s", e)

        if source == "game":
            rcon = self._get_rcon()
            if rcon is not None:
                try:
                    await rcon.execute(f"admin {plain_text}", read_timeout=1.5)
                except Exception as e:
                    logger.error("Failed to send RCON response: %s", e)

    async def _trigger_parse(self, save: SaveService) -> None:
        """背景觸發存檔解析（由指令的 stale 檢查觸發）。"""
        try:
            success = await save.parse_save()
            if success:
                logger.info("On-demand save parse triggered by command completed")
            else:
                logger.warning("On-demand save parse failed")
        except Exception:
            logger.exception("On-demand save parse error")

    # === 指令處理器 ===

    async def _cmd_coords(
        self, player_name: str, locale: str
    ) -> tuple[discord.Embed, str]:
        """!coords / !位置 — 顯示玩家座標。"""
        save = self._get_save_service()
        identity = self._get_identity_service()

        if save is None or not save.is_available:
            return self._error_response("cmd.no_save_data", locale)

        if identity is None:
            return self._error_response("cmd.no_save_data", locale)

        # 透過 identity service 取得 steam_id
        steam_id = await asyncio.to_thread(identity.get_steam_id, player_name)
        if steam_id is None:
            return self._error_response(
                "cmd.player_not_found", locale, name=player_name
            )

        player = await save.get_player(steam_id)
        if player is None:
            return self._error_response(
                "cmd.player_not_found", locale, name=player_name
            )

        # 建立 embed
        title = _t("cmd.coords.title", locale, name=player_name)
        value = _t("cmd.coords.value", locale, x=player.x, y=player.y, z=player.z)
        note = _t("cmd.coords.note", locale)

        # 取得解析時間
        parse_time_str = ""
        meta = await save.get_parse_meta()
        if meta and meta.get("last_parse_time"):
            try:
                dt = datetime.fromisoformat(meta["last_parse_time"])
                parse_time_str = _t("cmd.coords.parse_time", locale, time=dt.strftime("%m/%d %H:%M"))
            except (ValueError, TypeError):
                pass

        embed = discord.Embed(title=title, color=_COLOR_INFO)
        parts = [value, "", note]
        if parse_time_str:
            parts.append(parse_time_str)
        embed.description = "\n".join(parts)

        # RCON 純文字
        plain = _t("cmd.plain.coords", locale, name=player_name, x=player.x, y=player.y, z=player.z)

        return embed, plain

    async def _cmd_stats(
        self, player_name: str, locale: str
    ) -> tuple[discord.Embed, str]:
        """!stats / !狀態 — 顯示玩家生存狀態。"""
        save = self._get_save_service()
        identity = self._get_identity_service()

        if save is None or not save.is_available:
            return self._error_response("cmd.no_save_data", locale)

        if identity is None:
            return self._error_response("cmd.no_save_data", locale)

        steam_id = await asyncio.to_thread(identity.get_steam_id, player_name)
        if steam_id is None:
            return self._error_response(
                "cmd.player_not_found", locale, name=player_name
            )

        player = await save.get_player(steam_id)
        if player is None:
            return self._error_response(
                "cmd.player_not_found", locale, name=player_name
            )

        # 建立 embed
        title = _t("cmd.stats.title", locale, name=player_name)
        lines = [
            _t("cmd.stats.health", locale, health=player.health),
            _t("cmd.stats.hunger", locale, hunger=player.hunger),
            _t("cmd.stats.thirst", locale, thirst=player.thirst),
            _t("cmd.stats.stamina", locale, stamina=player.stamina),
            _t("cmd.stats.infection", locale, infection=player.infection),
            _t("cmd.stats.bites", locale, bites=player.bites),
            _t("cmd.stats.survival_days", locale, days=player.survival_days),
        ]
        if player.profession:
            lines.append(
                _t("cmd.stats.profession", locale, profession=player.profession)
            )
        # 擊殺明細（無論是否為 0 都顯示總數，明細只顯示非零）
        lines.append(
            _t("cmd.stats.kills_summary", locale,
               zombies=player.zombies_killed, headshots=player.headshots)
        )
        detail = _build_kill_detail(player, locale)
        if detail:
            lines.append(f"　　{detail}")
        lines.append(_t("cmd.stats.fish", locale, fish=player.fish_caught))

        embed = discord.Embed(
            title=title,
            description="\n".join(lines),
            color=_COLOR_INFO,
        )

        # RCON 純文字
        plain_parts = [
            _t("cmd.plain.stats", locale,
               name=player_name, health=player.health,
               hunger=player.hunger, thirst=player.thirst,
               days=player.survival_days),
        ]
        plain_parts.append(
            _t("cmd.plain.stats_kills", locale,
               zombies=player.zombies_killed, headshots=player.headshots)
        )
        plain = " | ".join(plain_parts)

        return embed, plain

    async def _cmd_top(self, locale: str) -> tuple[discord.Embed, str]:
        """!top / !排行 — 存活天數排行榜。"""
        save = self._get_save_service()
        identity = self._get_identity_service()

        if save is None or not save.is_available:
            return self._error_response("cmd.no_save_data", locale)

        limit = 10
        leaderboard = await save.get_leaderboard(limit)

        if not leaderboard:
            return self._error_response("cmd.top.no_data", locale)

        # 建立排行榜文字
        entries: list[str] = []
        plain_entries: list[str] = []
        for rank, player in enumerate(leaderboard, start=1):
            # 嘗試解析 steam_id → 玩家名稱
            name = player.player_name
            if not name and identity is not None:
                name = await asyncio.to_thread(identity.get_player_name, player.steam_id) or ""

            # Discord embed: Name (steam_id) 或 steam_id
            if name:
                display_name = name
            else:
                display_name = player.steam_id

            entry = _t(
                "cmd.top.entry", locale,
                rank=rank, name=display_name, steam_id=player.steam_id,
                days=player.survival_days,
            )
            entries.append(entry)

            # RCON 純文字: 只顯示名稱（或截斷 ID）
            rcon_name = name if name else player.steam_id[:12] + "..."
            plain_entries.append(_t(
                "cmd.plain.top_entry", locale,
                rank=rank, name=rcon_name, days=player.survival_days,
            ))

        title = _t("cmd.top.title", locale, limit=limit)
        embed = discord.Embed(
            title=title,
            description="\n".join(entries),
            color=_COLOR_INFO,
        )

        # RCON: 直接從 #1 開始列出，用 / 分隔
        plain = " / ".join(plain_entries)

        return embed, plain

    async def _cmd_kills(self, locale: str) -> tuple[discord.Embed, str]:
        """!kills / !擊殺 — 擊殺數排行榜。"""
        save = self._get_save_service()
        identity = self._get_identity_service()

        if save is None or not save.is_available:
            return self._error_response("cmd.no_save_data", locale)

        limit = 10
        leaderboard = await save.get_kill_leaderboard(limit)

        if not leaderboard:
            return self._error_response("cmd.kills.no_data", locale)

        # 過濾掉 0 擊殺的玩家
        leaderboard = [p for p in leaderboard if p.zombies_killed > 0]
        if not leaderboard:
            return self._error_response("cmd.kills.no_data", locale)

        entries: list[str] = []
        plain_entries: list[str] = []
        for rank, player in enumerate(leaderboard, start=1):
            name = player.player_name
            if not name and identity is not None:
                name = await asyncio.to_thread(identity.get_player_name, player.steam_id) or ""

            display_name = name if name else player.steam_id

            # Discord embed: 主行 + 動態明細行
            entry = _t(
                "cmd.kills.entry", locale,
                rank=rank, name=display_name,
                zombies=player.zombies_killed, headshots=player.headshots,
            )
            detail = _build_kill_detail(player, locale)
            if detail:
                entry += f"\n　　{detail}"
            entries.append(entry)

            # RCON 純文字: 簡潔
            rcon_name = name if name else player.steam_id[:12] + "..."
            plain_entries.append(_t(
                "cmd.plain.kills_entry", locale,
                rank=rank, name=rcon_name, zombies=player.zombies_killed,
            ))

        title = _t("cmd.kills.title", locale, limit=limit)
        embed = discord.Embed(
            title=title,
            description="\n\n".join(entries),
            color=_COLOR_INFO,
        )

        plain = " / ".join(plain_entries)

        return embed, plain

    async def _cmd_server(self, locale: str) -> tuple[discord.Embed, str]:
        """!server / !伺服器 — 伺服器遊戲狀態。"""
        save = self._get_save_service()

        if save is None or not save.is_available:
            return self._error_response("cmd.no_save_data", locale)

        game_state = await save.get_game_state()
        meta = await save.get_parse_meta()

        title = _t("cmd.server.title", locale)

        lines: list[str] = []
        plain_parts: list[str] = []

        if game_state is not None:
            lines.append(
                _t("cmd.server.days_passed", locale, days=game_state.days_passed)
            )
            lines.append(_t("cmd.server.season_day", locale, day=game_state.season_day))
            plain_parts.append(f"Days:{game_state.days_passed}")
            plain_parts.append(f"SeasonDay:{game_state.season_day}")

        if meta is not None:
            player_count = meta.get("player_count", 0)
            lines.append(_t("cmd.server.players_in_save", locale, count=player_count))
            plain_parts.append(f"Players:{player_count}")

            last_parse = meta.get("last_parse_time", "")
            if last_parse:
                # 格式化時間顯示
                try:
                    dt = datetime.fromisoformat(last_parse)
                    formatted_time = dt.strftime("%Y/%m/%d %H:%M:%S")
                except (ValueError, TypeError):
                    formatted_time = last_parse
                lines.append(_t("cmd.server.last_parse", locale, time=formatted_time))

        embed = discord.Embed(
            title=title,
            description="\n".join(lines) if lines else _t("cmd.no_save_data", locale),
            color=_COLOR_INFO,
        )

        plain = _t("cmd.plain.server", locale, parts=" | ".join(plain_parts)) if plain_parts else _t("cmd.plain.server.no_data", locale)

        return embed, plain

    async def _cmd_help(self, locale: str) -> tuple[discord.Embed, str]:
        """!help / !幫助 — 顯示可用指令列表。"""
        title = _t("cmd.help.title", locale)
        desc = _t("cmd.help.description", locale)

        lines = [
            desc,
            "",
            _t("cmd.help.coords", locale),
            _t("cmd.help.stats", locale),
            _t("cmd.help.top", locale),
            _t("cmd.help.kills", locale),
            _t("cmd.help.server", locale),
            _t("cmd.help.help", locale),
        ]

        embed = discord.Embed(
            title=title,
            description="\n".join(lines),
            color=_COLOR_INFO,
        )

        # RCON 純文字
        plain = _t("cmd.plain.help", locale)

        return embed, plain

    # === 工具方法 ===

    @staticmethod
    def _error_response(
        key: str, locale: str, **kwargs: object
    ) -> tuple[discord.Embed, str]:
        """建立錯誤回應（embed + 純文字）。"""
        text = _t(key, locale, **kwargs)
        embed = discord.Embed(description=text, color=_COLOR_ERROR)
        # 純文字版本去除 emoji（RCON 不支援）
        plain = re.sub(r'[\U00010000-\U0010ffff]', '', text).strip()
        return embed, plain


async def setup(bot: commands.Bot) -> None:
    """載入 GameCommandsCog。"""
    await bot.add_cog(GameCommandsCog(bot))
