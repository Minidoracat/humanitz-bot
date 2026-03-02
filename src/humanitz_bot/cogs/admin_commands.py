"""管理員指令 Cog — 處理遊戲伺服器管理指令（踢人/封鎖/重啟等）。"""

from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord.ext import commands

from humanitz_bot.services.player_identity import (
    PlayerIdentityInfo,
    PlayerIdentityService,
)
from humanitz_bot.services.rcon_service import RconService
from humanitz_bot.utils.i18n import _STRINGS

logger = logging.getLogger("humanitz_bot.cogs.admin_commands")

_COLOR_SUCCESS = 0x2ECC71
_COLOR_ERROR = 0xE74C3C
_COLOR_WARNING = 0xF39C12

_ADMIN_COOLDOWN_SECONDS = 3

# alias → (command_name, locale)
_ADMIN_COMMAND_ALIASES: dict[str, tuple[str, str]] = {
    # 玩家管理
    "kick": ("kick", "en"),
    "踢出": ("kick", "zh-TW"),
    "ban": ("ban", "en"),
    "封鎖": ("ban", "zh-TW"),
    "unban": ("unban", "en"),
    "解封": ("unban", "zh-TW"),
    "teleport": ("teleport", "en"),
    "傳送": ("teleport", "zh-TW"),
    "unstuck": ("unstuck", "en"),
    "解卡": ("unstuck", "zh-TW"),
    "tptoplayer": ("tptoplayer", "en"),
    "傳送到玩家": ("tptoplayer", "zh-TW"),
    "fixcar": ("fixcar", "en"),
    "修車": ("fixcar", "zh-TW"),
    # 伺服器控制
    "save": ("save", "en"),
    "存檔": ("save", "zh-TW"),
    "restart": ("restart", "en"),
    "重啟": ("restart", "zh-TW"),
    "quickrestart": ("quickrestart", "en"),
    "快速重啟": ("quickrestart", "zh-TW"),
    "restartnow": ("restartnow", "en"),
    "立即重啟": ("restartnow", "zh-TW"),
    "cancelrestart": ("cancelrestart", "en"),
    "取消重啟": ("cancelrestart", "zh-TW"),
    "shutdown": ("shutdown", "en"),
    "關機": ("shutdown", "zh-TW"),
}

# 需要玩家目標的指令
_PLAYER_COMMANDS = frozenset({"kick", "ban", "teleport", "unstuck", "fixcar"})

# 不需要參數的伺服器指令
_SERVER_COMMANDS = frozenset(
    {"save", "quickrestart", "restartnow", "cancelrestart", "shutdown"}
)

# 指令 → RCON 指令名稱（大小寫需精確）
_RCON_COMMAND_MAP: dict[str, str] = {
    "kick": "kick",
    "ban": "ban",
    "unban": "unban",
    "teleport": "teleport",
    "unstuck": "unstuck",
    "tptoplayer": "tptoplayer",
    "fixcar": "fixcar",
    "save": "save",
    "restart": "restart",
    "quickrestart": "QuickRestart",
    "restartnow": "RestartNow",
    "cancelrestart": "CancelRestart",
    "shutdown": "shutdown",
}


def _t(key: str, locale: str, **kwargs: object) -> str:
    """取得指定語系的翻譯字串。"""
    table = _STRINGS.get(locale, _STRINGS["en"])
    text = table.get(key) or _STRINGS["en"].get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text


# ──────────────────────────────────────────────
# Discord UI Views
# ──────────────────────────────────────────────


class PlayerSelectView(discord.ui.View):
    """多個玩家匹配時的 Discord Select Menu。"""

    def __init__(
        self,
        players: list[PlayerIdentityInfo],
        rcon_command: str,
        rcon: RconService,
        locale: str,
        admin_id: int,
    ) -> None:
        super().__init__(timeout=30)
        self._rcon = rcon
        self._rcon_command = rcon_command
        self._locale = locale
        self._admin_id = admin_id
        self._responded = False

        options = [
            discord.SelectOption(
                label=(p.player_name or p.steam_id)[:100],
                value=p.steam_id,
                description=f"Steam ID: {p.steam_id}"[:100],
            )
            for p in players[:25]
        ]

        select = discord.ui.Select(
            placeholder=_t("admin.select.player_placeholder", locale),
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        """玩家選擇後執行 RCON 指令。"""
        if interaction.user.id != self._admin_id:
            await interaction.response.send_message(
                _t("admin.not_authorized", self._locale),
                ephemeral=True,
            )
            return
        if self._responded:
            return
        self._responded = True

        # Defer — RCON 可能超過 Discord 3 秒互動限制
        await interaction.response.defer()

        steam_id: str = interaction.data["values"][0]  # type: ignore[index]
        full_command = f"{self._rcon_command} {steam_id}"

        # 取得選中玩家的顯示名稱
        display_name = steam_id
        for child in self.children:
            if isinstance(child, discord.ui.Select):
                for opt in child.options:
                    if opt.value == steam_id:
                        display_name = opt.label
                        break

        try:
            response = await self._rcon.execute(full_command, read_timeout=5.0)
            embed = discord.Embed(
                description=_t(
                    "admin.command_sent_detail",
                    self._locale,
                    command=self._rcon_command,
                    name=display_name,
                    steam_id=steam_id,
                ),
                color=_COLOR_SUCCESS,
            )
            if response:
                embed.add_field(
                    name=_t("admin.embed_response", self._locale),
                    value=response[:1024],
                    inline=False,
                )
            await interaction.edit_original_response(embed=embed, view=None)
        except Exception:
            logger.exception("RCON command failed via select: %s", full_command)
            try:
                embed = discord.Embed(
                    description=_t("admin.command_error", self._locale),
                    color=_COLOR_ERROR,
                )
                await interaction.edit_original_response(embed=embed, view=None)
            except discord.HTTPException:
                pass

    async def on_timeout(self) -> None:
        """選單超時 — 更新訊息提示已過期。"""
        self._responded = True
        if self.message is not None:
            try:
                embed = discord.Embed(
                    description=_t("admin.select_expired", self._locale),
                    color=_COLOR_WARNING,
                )
                await self.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass


class TpToPlayerSelectView(discord.ui.View):
    """tptoplayer 的玩家選擇 Menu — 解析其中一個模糊目標。"""

    def __init__(
        self,
        players: list[PlayerIdentityInfo],
        other_steam_id: str,
        is_target: bool,
        rcon: RconService,
        locale: str,
        admin_id: int,
    ) -> None:
        super().__init__(timeout=30)
        self._rcon = rcon
        self._other_steam_id = other_steam_id
        self._is_target = is_target
        self._locale = locale
        self._admin_id = admin_id
        self._responded = False

        role_label = _t(
            "admin.select.role_target" if is_target else "admin.select.role_destination",
            locale,
        )

        options = [
            discord.SelectOption(
                label=(p.player_name or p.steam_id)[:100],
                value=p.steam_id,
                description=f"Steam ID: {p.steam_id}"[:100],
            )
            for p in players[:25]
        ]

        select = discord.ui.Select(
            placeholder=_t("admin.select.role_placeholder", locale, role=role_label),
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._admin_id:
            await interaction.response.send_message(
                _t("admin.not_authorized", self._locale),
                ephemeral=True,
            )
            return
        if self._responded:
            return
        self._responded = True

        # Defer — RCON 可能超過 Discord 3 秒互動限制
        await interaction.response.defer()

        selected: str = interaction.data["values"][0]  # type: ignore[index]

        if self._is_target:
            full_command = f"tptoplayer {selected} {self._other_steam_id}"
        else:
            full_command = f"tptoplayer {self._other_steam_id} {selected}"

        try:
            response = await self._rcon.execute(full_command, read_timeout=5.0)
            embed = discord.Embed(
                description=_t(
                    "admin.command_sent", self._locale, command=full_command
                ),
                color=_COLOR_SUCCESS,
            )
            if response:
                embed.add_field(
                    name=_t("admin.embed_response", self._locale),
                    value=response[:1024],
                    inline=False,
                )
            await interaction.edit_original_response(embed=embed, view=None)
        except Exception:
            logger.exception("RCON tptoplayer failed via select: %s", full_command)
            try:
                embed = discord.Embed(
                    description=_t("admin.command_error", self._locale),
                    color=_COLOR_ERROR,
                )
                await interaction.edit_original_response(embed=embed, view=None)
            except discord.HTTPException:
                pass

    async def on_timeout(self) -> None:
        """選單超時 — 更新訊息提示已過期。"""
        self._responded = True
        if self.message is not None:
            try:
                embed = discord.Embed(
                    description=_t("admin.select_expired", self._locale),
                    color=_COLOR_WARNING,
                )
                await self.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass


# ──────────────────────────────────────────────
# Main Cog
# ──────────────────────────────────────────────


class AdminCommandsCog(commands.Cog):
    """管理員指令 — 玩家管理與伺服器控制。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        settings = bot.settings  # type: ignore[attr-defined]

        self._admin_discord_ids: set[str] = set(settings.admin_discord_ids)
        self._admin_game_ids: set[str] = set(settings.admin_game_ids)
        self._cooldowns: dict[str, float] = {}

        # 獨立 RCON 連線 — 管理指令不與 status/chat 爭搶鎖
        self._rcon = RconService(
            settings.rcon_host,
            settings.rcon_port,
            settings.rcon_password,
        )

    # --- 服務存取 ---

    def _get_identity_service(self) -> PlayerIdentityService | None:
        """從 ServerStatusCog 取得 PlayerIdentityService 實例。"""
        status_cog = self.bot.get_cog("ServerStatusCog")
        if status_cog is not None:
            return getattr(status_cog, "identity_service", None)
        return None

    # --- 權限檢查 ---

    def _is_discord_admin(self, user_id: int) -> bool:
        return str(user_id) in self._admin_discord_ids

    def _is_game_admin(self, steam_id: str) -> bool:
        return steam_id in self._admin_game_ids

    async def check_game_admin(self, player_name: str) -> bool:
        """透過玩家名稱查找 Steam ID 並檢查管理員權限。

        注意：依賴 PlayerIdentityService 的名稱快取（每 30 秒由 status loop 更新）。
        若快取過期或存在重名玩家，可能產生誤判。建議搭配 Discord 管理員 ID 使用。
        """
        identity = self._get_identity_service()
        if identity is None:
            return False
        steam_id = await asyncio.to_thread(identity.get_steam_id, player_name)
        if steam_id is None:
            return False
        return self._is_game_admin(steam_id)

    def is_admin(
        self,
        source: str,
        message: discord.Message | None,
        player_name: str,
    ) -> bool:
        """同步管理員檢查（僅 Discord 來源，遊戲來源需 async）。"""
        if source == "discord" and message is not None:
            return self._is_discord_admin(message.author.id)
        return False

    # --- 冷卻 ---

    def _check_cooldown(self, key: str) -> float:
        """檢查指令冷卻。回傳剩餘冷卻秒數，0 表示可執行。"""
        now = time.time()
        last = self._cooldowns.get(key, 0.0)
        remaining = _ADMIN_COOLDOWN_SECONDS - (now - last)
        if remaining > 0:
            return remaining
        self._cooldowns[key] = now
        # 順便清理過期條目（避免無限增長）
        cutoff = now - _ADMIN_COOLDOWN_SECONDS
        expired = [k for k, v in self._cooldowns.items() if v < cutoff and k != key]
        for k in expired:
            del self._cooldowns[k]
        return 0.0

    # --- 指令識別 ---

    @staticmethod
    def is_admin_command(alias: str) -> bool:
        """檢查指令別名是否為管理員指令。"""
        return alias in _ADMIN_COMMAND_ALIASES

    # --- 主進入點 ---

    async def handle_command(
        self,
        player_name: str,
        command_text: str,
        channel: discord.TextChannel,
        source: str = "game",
        message: discord.Message | None = None,
    ) -> bool:
        """處理管理員指令。

        Returns:
            True 如果指令已被處理，False 如果不是管理員指令。
        """
        raw = command_text.lstrip("!").strip()
        if not raw:
            return False

        parts = raw.split()
        alias = parts[0]
        args = parts[1:] if len(parts) > 1 else []

        mapping = _ADMIN_COMMAND_ALIASES.get(alias)
        if mapping is None:
            return False

        cmd_name, locale = mapping
        logger.debug("Admin command matched: alias=%s → cmd=%s, locale=%s, source=%s, player=%s", alias, cmd_name, locale, source, player_name)

        # 權限檢查
        is_admin = False
        if source == "discord" and message is not None:
            is_admin = self._is_discord_admin(message.author.id)
        elif source == "game":
            is_admin = await self.check_game_admin(player_name)

        if not is_admin:
            # 對非管理員不揭露管理指令的存在
            return False
        logger.debug("Admin permission denied: source=%s, player=%s", source, player_name)

        # 冷卻檢查
        cooldown_key = str(message.author.id) if message is not None else player_name
        remaining = self._check_cooldown(cooldown_key)
        if remaining > 0:
            if source == "discord":
                embed = discord.Embed(
                    description=_t(
                        "admin.cooldown", locale, seconds=int(remaining) + 1
                    ),
                    color=_COLOR_ERROR,
                )
                await channel.send(embed=embed)
            return True

        logger.debug("Admin routing: cmd=%s, args=%s", cmd_name, args)
        # 路由到處理器
        try:
            if cmd_name in _PLAYER_COMMANDS:
                await self._handle_player_command(
                    cmd_name,
                    args,
                    channel,
                    source,
                    locale,
                    message,
                )
            elif cmd_name == "tptoplayer":
                await self._handle_tptoplayer(
                    args,
                    channel,
                    source,
                    locale,
                    message,
                )
            elif cmd_name == "unban":
                await self._handle_unban(args, channel, source, locale)
            elif cmd_name == "restart":
                await self._handle_restart(args, channel, source, locale)
            elif cmd_name in _SERVER_COMMANDS:
                await self._handle_server_command(cmd_name, channel, source, locale)
            else:
                return False
        except Exception:
            logger.exception("Admin command handler failed: %s", cmd_name)
            try:
                await channel.send(
                    embed=discord.Embed(
                        description=_t("admin.command_error", locale),
                        color=_COLOR_ERROR,
                    )
                )
            except Exception:
                logger.exception("Failed to send error feedback for: %s", cmd_name)

        return True
        logger.info("Admin command completed: %s (by %s via %s)", cmd_name, player_name if source == 'game' else getattr(message, 'author', '?'), source)

    # --- 玩家管理指令 ---

    async def _handle_player_command(
        self,
        cmd_name: str,
        args: list[str],
        channel: discord.TextChannel,
        source: str,
        locale: str,
        message: discord.Message | None,
    ) -> None:
        """處理需要玩家目標的指令（kick/ban/teleport/unstuck/fixcar）。"""
        if not args:
            embed = discord.Embed(
                description=_t("admin.usage.player_command", locale, command=cmd_name),
                color=_COLOR_ERROR,
            )
            await channel.send(embed=embed)
            return

        query = " ".join(args)
        identity = self._get_identity_service()
        if identity is None:
            await channel.send(
                embed=discord.Embed(
                    description=_t("admin.player_not_found", locale, query=query),
                    color=_COLOR_ERROR,
                )
            )
            return

        matches = await asyncio.to_thread(identity.resolve_player, query)
        rcon_cmd = _RCON_COMMAND_MAP[cmd_name]

        if not matches:
            await channel.send(
                embed=discord.Embed(
                    description=_t("admin.player_not_found", locale, query=query),
                    color=_COLOR_ERROR,
                )
            )
            return

        if len(matches) == 1:
            player = matches[0]
            await self._execute_and_respond(
                f"{rcon_cmd} {player.steam_id}",
                channel,
                source,
                locale,
                player.player_name,
                player.steam_id,
            )
            return

        # 多重匹配 — 需要消歧
        if source == "discord" and message is not None:
            embed = discord.Embed(
                description=_t("admin.multiple_matches", locale, query=query),
                color=_COLOR_WARNING,
            )
            view = PlayerSelectView(
                players=matches,
                rcon_command=rcon_cmd,
                rcon=self._rcon,
                locale=locale,
                admin_id=message.author.id,
            )
            msg = await channel.send(embed=embed, view=view)
            view.message = msg
        else:
            player_list = ", ".join(
                f"{p.player_name} ({p.steam_id})" for p in matches[:5]
            )
            plain = _t(
                "admin.multiple_matches_game",
                locale,
                query=query,
                players=player_list,
            )
            await self._rcon.execute(f"admin {plain}", read_timeout=1.5)

    # --- tptoplayer ---

    async def _handle_tptoplayer(
        self,
        args: list[str],
        channel: discord.TextChannel,
        source: str,
        locale: str,
        message: discord.Message | None,
    ) -> None:
        """處理 tptoplayer — 需要兩個玩家參數。"""
        if len(args) < 2:
            await channel.send(
                embed=discord.Embed(
                    description=_t("admin.usage.tptoplayer", locale),
                    color=_COLOR_ERROR,
                )
            )
            return

        identity = self._get_identity_service()
        if identity is None:
            await channel.send(
                embed=discord.Embed(
                    description=_t("admin.player_not_found", locale, query=args[0]),
                    color=_COLOR_ERROR,
                )
            )
            return

        target_matches = await asyncio.to_thread(identity.resolve_player, args[0])
        dest_matches = await asyncio.to_thread(identity.resolve_player, args[1])

        if not target_matches:
            await channel.send(
                embed=discord.Embed(
                    description=_t("admin.player_not_found", locale, query=args[0]),
                    color=_COLOR_ERROR,
                )
            )
            return
        if not dest_matches:
            await channel.send(
                embed=discord.Embed(
                    description=_t("admin.player_not_found", locale, query=args[1]),
                    color=_COLOR_ERROR,
                )
            )
            return

        # 兩者都唯一 — 直接執行
        if len(target_matches) == 1 and len(dest_matches) == 1:
            t_p, d_p = target_matches[0], dest_matches[0]
            full_cmd = f"tptoplayer {t_p.steam_id} {d_p.steam_id}"
            try:
                response = await self._rcon.execute(full_cmd, read_timeout=5.0)
                embed = discord.Embed(
                    description=_t(
                        "admin.command_sent_detail",
                        locale,
                        command="tptoplayer",
                        name=f"{t_p.player_name} → {d_p.player_name}",
                        steam_id=f"{t_p.steam_id} → {d_p.steam_id}",
                    ),
                    color=_COLOR_SUCCESS,
                )
                if response:
                    embed.add_field(
                        name=_t("admin.embed_response", locale),
                        value=response[:1024],
                        inline=False,
                    )
                await channel.send(embed=embed)
                if source == "game":
                    try:
                        await self._rcon.execute(
                            f"admin {_t('admin.game_tptoplayer_echo', locale, target=t_p.player_name, dest=d_p.player_name)}",
                            read_timeout=1.5,
                        )
                    except Exception:
                        logger.warning("Game echo failed for tptoplayer")
            except Exception:
                logger.exception("RCON tptoplayer failed: %s", full_cmd)
                await channel.send(
                    embed=discord.Embed(
                        description=_t("admin.command_error", locale),
                        color=_COLOR_ERROR,
                    )
                )
            return

        # Discord 消歧
        if source == "discord" and message is not None:
            if len(target_matches) > 1 and len(dest_matches) > 1:
                # 兩個都模糊 — 要求指定 Steam ID
                t_list = ", ".join(
                    f"**{p.player_name}** (`{p.steam_id}`)" for p in target_matches[:5]
                )
                d_list = ", ".join(
                    f"**{p.player_name}** (`{p.steam_id}`)" for p in dest_matches[:5]
                )
                await channel.send(
                    embed=discord.Embed(
                        description=_t(
                            "admin.both_ambiguous",
                            locale,
                            target=args[0],
                            target_list=t_list,
                            dest=args[1],
                            dest_list=d_list,
                        ),
                        color=_COLOR_ERROR,
                    )
                )
            elif len(target_matches) > 1:
                view = TpToPlayerSelectView(
                    players=target_matches,
                    other_steam_id=dest_matches[0].steam_id,
                    is_target=True,
                    rcon=self._rcon,
                    locale=locale,
                    admin_id=message.author.id,
                )
                msg = await channel.send(
                    embed=discord.Embed(
                        description=_t("admin.multiple_matches", locale, query=args[0]),
                        color=_COLOR_WARNING,
                    ),
                    view=view,
                )
                view.message = msg
            else:
                view = TpToPlayerSelectView(
                    players=dest_matches,
                    other_steam_id=target_matches[0].steam_id,
                    is_target=False,
                    rcon=self._rcon,
                    locale=locale,
                    admin_id=message.author.id,
                )
                msg = await channel.send(
                    embed=discord.Embed(
                        description=_t("admin.multiple_matches", locale, query=args[1]),
                        color=_COLOR_WARNING,
                    ),
                    view=view,
                )
                view.message = msg
        else:
            # 遊戲來源 — 列出匹配並要求指定
            parts: list[str] = []
            if len(target_matches) > 1:
                parts.append(
                    _t("admin.game_target_label", locale, name=args[0], players=", ".join(
                        f"{p.player_name}({p.steam_id})" for p in target_matches[:5]
                    ))
                )
            if len(dest_matches) > 1:
                parts.append(
                    _t("admin.game_dest_label", locale, name=args[1], players=", ".join(
                        f"{p.player_name}({p.steam_id})" for p in dest_matches[:5]
                    ))
                )
            await self._rcon.execute(
                f"admin {_t('admin.game_specify_steamid', locale, parts=' | '.join(parts))}",
                read_timeout=1.5,
            )

    # --- unban ---

    async def _handle_unban(
        self,
        args: list[str],
        channel: discord.TextChannel,
        source: str,
        locale: str,
    ) -> None:
        """處理 unban — 支援 Steam ID 或玩家名稱。"""
        if not args:
            await channel.send(
                embed=discord.Embed(
                    description=_t("admin.usage.unban", locale),
                    color=_COLOR_ERROR,
                )
            )
            return

        query = " ".join(args)

        # 如果不像 Steam ID，嘗試解析玩家名稱
        if not query.isdigit() or len(query) < 10:
            identity = self._get_identity_service()
            if identity is not None:
                matches = await asyncio.to_thread(identity.resolve_player, query)
                if len(matches) == 1:
                    query = matches[0].steam_id
                elif len(matches) > 1:
                    player_list = "\n".join(
                        f"• **{p.player_name}** — `{p.steam_id}`" for p in matches[:10]
                    )
                    embed = discord.Embed(
                        description=_t("admin.multiple_matches", locale, query=query),
                        color=_COLOR_WARNING,
                    )
                    embed.add_field(
                        name=_t("admin.matched_players", locale),
                        value=player_list,
                        inline=False,
                    )
                    await channel.send(embed=embed)
                    return
                else:
                    await channel.send(
                        embed=discord.Embed(
                            description=_t(
                                "admin.player_not_found", locale, query=query
                            ),
                            color=_COLOR_ERROR,
                        )
                    )
                    return

        await self._execute_and_respond(
            f"unban {query}",
            channel,
            source,
            locale,
            query,
            query,
        )

    # --- restart ---

    async def _handle_restart(
        self,
        args: list[str],
        channel: discord.TextChannel,
        source: str,
        locale: str,
    ) -> None:
        """處理 restart X — 需要分鐘數。"""
        if not args:
            await channel.send(
                embed=discord.Embed(
                    description=_t("admin.usage.restart", locale),
                    color=_COLOR_ERROR,
                )
            )
            return

        try:
            minutes = int(args[0])
        except ValueError:
            await channel.send(
                embed=discord.Embed(
                    description=_t("admin.invalid_minutes", locale, value=args[0]),
                    color=_COLOR_ERROR,
                )
            )
            return

        if minutes < 1 or minutes > 1440:
            await channel.send(
                embed=discord.Embed(
                    description=_t("admin.invalid_minutes", locale, value=str(minutes)),
                    color=_COLOR_ERROR,
                )
            )
            return

        response = await self._rcon.execute(f"restart {minutes}", read_timeout=5.0)
        embed = discord.Embed(
            description=_t("admin.restart_scheduled", locale, minutes=minutes),
            color=_COLOR_SUCCESS,
        )
        if response:
            embed.add_field(
                name=_t("admin.embed_response", locale),
                value=response[:1024],
                inline=False,
            )
        await channel.send(embed=embed)

        if source == "game":
            try:
                await self._rcon.execute(
                    f"admin {_t('admin.game_restart_echo', locale, minutes=minutes)}",
                    read_timeout=1.5,
                )
            except Exception:
                logger.warning("Game echo failed for restart")

    # --- 伺服器控制（無參數） ---

    async def _handle_server_command(
        self,
        cmd_name: str,
        channel: discord.TextChannel,
        source: str,
        locale: str,
    ) -> None:
        """處理 save/quickrestart/restartnow/cancelrestart/shutdown。"""
        rcon_cmd = _RCON_COMMAND_MAP[cmd_name]
        response = await self._rcon.execute(rcon_cmd, read_timeout=5.0)

        if response:
            embed = discord.Embed(
                description=_t(
                    "admin.server_command_response",
                    locale,
                    command=rcon_cmd,
                    response=response[:500],
                ),
                color=_COLOR_SUCCESS,
            )
        else:
            embed = discord.Embed(
                description=_t("admin.server_command_sent", locale, command=rcon_cmd),
                color=_COLOR_SUCCESS,
            )
        await channel.send(embed=embed)

        if source == "game":
            try:
                await self._rcon.execute(
                    f"admin {_t('admin.game_server_echo', locale, command=rcon_cmd)}",
                    read_timeout=1.5,
                )
            except Exception:
                logger.warning("Game echo failed for %s", cmd_name)

    # --- 共用工具 ---

    async def _execute_and_respond(
        self,
        full_command: str,
        channel: discord.TextChannel,
        source: str,
        locale: str,
        player_name: str,
        steam_id: str,
    ) -> None:
        """執行 RCON 指令並傳送成功/失敗回應。"""
        try:
            response = await self._rcon.execute(full_command, read_timeout=5.0)
            logger.debug("RCON execute result: cmd=%s → %r", full_command, response[:200] if response else None)
            rcon_cmd = full_command.split()[0]
            embed = discord.Embed(
                description=_t(
                    "admin.command_sent_detail",
                    locale,
                    command=rcon_cmd,
                    name=player_name,
                    steam_id=steam_id,
                ),
                color=_COLOR_SUCCESS,
            )
            if response:
                embed.add_field(
                    name=_t("admin.embed_response", locale),
                    value=response[:1024],
                    inline=False,
                )
            await channel.send(embed=embed)

            if source == "game":
                try:
                    await self._rcon.execute(
                        f"admin {_t('admin.game_command_done_echo', locale, command=rcon_cmd, name=player_name, steam_id=steam_id)}",
                        read_timeout=1.5,
                    )
                except Exception:
                    logger.warning("Game echo failed for %s", full_command)
        except Exception:
            logger.exception("RCON command failed: %s", full_command)
            await channel.send(
                embed=discord.Embed(
                    description=_t("admin.command_error", locale),
                    color=_COLOR_ERROR,
                )
            )

    async def cog_unload(self) -> None:
        """Cog 卸載時關閉 RCON 連線。"""
        await self._rcon.close()


async def setup(bot: commands.Bot) -> None:
    """載入 AdminCommandsCog。"""
    await bot.add_cog(AdminCommandsCog(bot))
