"""設定管理 - 從 .env 載入設定並驗證"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("humanitz_bot.config")


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
    chat_poll_interval: int = 10
    chart_history_hours: int = 24
    log_level: str = "INFO"
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
        if not discord_token:
            missing_fields.append("DISCORD_TOKEN")

        rcon_password = os.getenv("RCON_PASSWORD", "").strip()
        if not rcon_password:
            missing_fields.append("RCON_PASSWORD")

        status_channel_id_str = os.getenv("STATUS_CHANNEL_ID", "").strip()
        if not status_channel_id_str:
            missing_fields.append("STATUS_CHANNEL_ID")

        chat_channel_id_str = os.getenv("CHAT_CHANNEL_ID", "").strip()
        if not chat_channel_id_str:
            missing_fields.append("CHAT_CHANNEL_ID")

        if missing_fields:
            raise ValueError(
                f"缺少必要設定欄位: {', '.join(missing_fields)}\n"
                f"請確認 .env 檔案包含所有必要設定。"
            )

        # 讀取選用欄位（含預設值）
        rcon_host = os.getenv("RCON_HOST", "127.0.0.1").strip()
        rcon_port_str = os.getenv("RCON_PORT", "8888").strip()
        status_message_id_str = os.getenv("STATUS_MESSAGE_ID", "").strip()
        status_update_interval_str = os.getenv("STATUS_UPDATE_INTERVAL", "30").strip()
        chat_poll_interval_str = os.getenv("CHAT_POLL_INTERVAL", "10").strip()
        chart_history_hours_str = os.getenv("CHART_HISTORY_HOURS", "24").strip()
        log_level = os.getenv("LOG_LEVEL", "INFO").strip()
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
            log_level=log_level,
            player_log_path=player_log_path,
        )
