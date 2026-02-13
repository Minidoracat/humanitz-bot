"""設定管理 - 從 .env 載入設定並驗證"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("humanitz_bot.config")

_PLACEHOLDER_PATTERNS = ("YOUR_", "PLACEHOLDER", "CHANGEME", "TODO", "REPLACE")


def _is_placeholder(value: str) -> bool:
    if not value:
        return False
    upper = value.upper()
    return any(
        upper.startswith(p) or upper.endswith("_HERE") for p in _PLACEHOLDER_PATTERNS
    )


@dataclass
class Settings:
    """Discord Bot 設定"""

    # 必要設定
    discord_token: str
    rcon_host: str
    rcon_port: int
    rcon_password: str
    status_channel_id: int
    chat_channel_id: int

    # 選用設定（含預設值）
    status_message_id: int | None = None  # None = bot 會建立新訊息
    status_update_interval: int = 30
    chat_poll_interval: int = 5
    chart_history_hours: int = 24
    locale: str = "en"
    db_retention_days: int = 30
    log_level: str = "INFO"
    log_retention_days: int = 7
    player_log_path: str = (
        "/home/hzserver/serverfiles/HumanitZServer/PlayerConnectedLog.txt"
    )

    @classmethod
    def from_env(cls, env_path: str | None = None) -> Settings:
        """
        從環境變數載入設定

        Args:
            env_path: .env 檔案路徑（None = 使用預設路徑）

        Returns:
            Settings 實例

        Raises:
            ValueError: 如果缺少必要設定
        """
        # 載入 .env
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()

        # 收集缺少的必要欄位
        missing_fields = []

        discord_token = os.getenv("DISCORD_TOKEN", "").strip()
        if not discord_token or _is_placeholder(discord_token):
            missing_fields.append("DISCORD_TOKEN")

        rcon_password = os.getenv("RCON_PASSWORD", "").strip()
        if not rcon_password or _is_placeholder(rcon_password):
            missing_fields.append("RCON_PASSWORD")

        status_channel_id_str = os.getenv("STATUS_CHANNEL_ID", "").strip()
        if not status_channel_id_str or status_channel_id_str == "0":
            missing_fields.append("STATUS_CHANNEL_ID")

        chat_channel_id_str = os.getenv("CHAT_CHANNEL_ID", "").strip()
        if not chat_channel_id_str or chat_channel_id_str == "0":
            missing_fields.append("CHAT_CHANNEL_ID")

        if missing_fields:
            error_msg = f"缺少必要設定欄位: {', '.join(missing_fields)}\n請編輯 .env 檔案填入真實值：\n"

            if "DISCORD_TOKEN" in missing_fields:
                error_msg += (
                    "  - DISCORD_TOKEN: 從 Discord Developer Portal 取得 Bot Token\n"
                )
            if "RCON_PASSWORD" in missing_fields:
                error_msg += "  - RCON_PASSWORD: HumanitZ 伺服器的 RCON 密碼\n"
            if "STATUS_CHANNEL_ID" in missing_fields:
                error_msg += (
                    "  - STATUS_CHANNEL_ID: Discord 頻道 ID（右鍵頻道 → 複製 ID）\n"
                )
            if "CHAT_CHANNEL_ID" in missing_fields:
                error_msg += (
                    "  - CHAT_CHANNEL_ID: Discord 頻道 ID（右鍵頻道 → 複製 ID）\n"
                )

            raise ValueError(error_msg.rstrip())

        # 讀取選用欄位（含預設值）
        rcon_host = os.getenv("RCON_HOST", "127.0.0.1").strip()
        rcon_port_str = os.getenv("RCON_PORT", "8888").strip()
        status_message_id_str = os.getenv("STATUS_MESSAGE_ID", "").strip()
        status_update_interval_str = os.getenv("STATUS_UPDATE_INTERVAL", "30").strip()
        chat_poll_interval_str = os.getenv("CHAT_POLL_INTERVAL", "5").strip()
        chart_history_hours_str = os.getenv("CHART_HISTORY_HOURS", "24").strip()
        locale = os.getenv("LOCALE", "en").strip()
        db_retention_days_str = os.getenv("DB_RETENTION_DAYS", "30").strip()
        log_level = os.getenv("LOG_LEVEL", "INFO").strip()
        log_retention_days_str = os.getenv("LOG_RETENTION_DAYS", "7").strip()
        player_log_path = os.getenv(
            "PLAYER_LOG_PATH",
            "/home/hzserver/serverfiles/HumanitZServer/PlayerConnectedLog.txt",
        ).strip()

        # 類型轉換
        try:
            rcon_port = int(rcon_port_str)
            status_channel_id = int(status_channel_id_str)
            chat_channel_id = int(chat_channel_id_str)
            status_message_id = (
                int(status_message_id_str) if status_message_id_str else None
            )
            status_update_interval = int(status_update_interval_str)
            chat_poll_interval = int(chat_poll_interval_str)
            chart_history_hours = int(chart_history_hours_str)
            db_retention_days = int(db_retention_days_str)
            log_retention_days = int(log_retention_days_str)
        except ValueError as e:
            raise ValueError(f"設定欄位類型轉換錯誤: {e}")

        return cls(
            discord_token=discord_token,
            rcon_host=rcon_host,
            rcon_port=rcon_port,
            rcon_password=rcon_password,
            status_channel_id=status_channel_id,
            chat_channel_id=chat_channel_id,
            status_message_id=status_message_id,
            status_update_interval=status_update_interval,
            chat_poll_interval=chat_poll_interval,
            chart_history_hours=chart_history_hours,
            locale=locale,
            db_retention_days=db_retention_days,
            log_level=log_level,
            log_retention_days=log_retention_days,
            player_log_path=player_log_path,
        )
