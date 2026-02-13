"""玩家人數歷史圖表服務 — 記錄 24 小時數據並生成折線圖。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 無頭伺服器必須在 import pyplot 之前設定

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

logger = logging.getLogger("humanitz_bot.services.chart_service")

_DISCORD_DARK = "#2b2d31"
_DISCORD_BLURPLE = "#5865F2"
_CHART_FILENAME = "player_chart.png"
_HISTORY_FILENAME = "player_history.json"


class ChartService:
    """記錄玩家人數歷史並生成 Discord 風格深色折線圖。"""

    def __init__(
        self, data_dir: str = "data", tmp_dir: str = "tmp", history_hours: int = 24
    ) -> None:
        self._max_age = timedelta(hours=history_hours)
        self._data_dir = Path(data_dir)
        self._tmp_dir = Path(tmp_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        self._history_path = self._data_dir / _HISTORY_FILENAME

    def _load_history(self) -> list[dict]:
        if not self._history_path.exists():
            return []
        try:
            with open(self._history_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("無法載入歷史數據: %s", e)
            return []

    def _save_history(self, data: list[dict]) -> None:
        try:
            with open(self._history_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except OSError as e:
            logger.error("無法儲存歷史數據: %s", e)

    def _prune_old_entries(self, data: list[dict]) -> list[dict]:
        cutoff = datetime.now(tz=timezone.utc) - self._max_age
        pruned = []
        for entry in data:
            try:
                ts = datetime.fromisoformat(entry["timestamp"])
                if ts >= cutoff:
                    pruned.append(entry)
            except (KeyError, ValueError):
                continue
        return pruned

    def add_data_point(self, player_count: int) -> None:
        """新增一筆玩家人數記錄，自動清除超過 24 小時的舊資料。"""
        history = self._load_history()
        history.append(
            {
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "count": player_count,
            }
        )
        history = self._prune_old_entries(history)
        self._save_history(history)

    def generate_chart(self) -> str | None:
        """生成 24 小時玩家人數折線圖，回傳 PNG 檔案路徑。

        無數據時回傳 None。
        """
        history = self._load_history()
        if not history:
            return None

        timestamps: list[datetime] = []
        counts: list[int] = []
        for entry in history:
            try:
                timestamps.append(datetime.fromisoformat(entry["timestamp"]))
                counts.append(entry["count"])
            except (KeyError, ValueError):
                continue

        if not timestamps:
            return None

        try:
            fig, ax = plt.subplots(figsize=(10, 3))
            fig.set_facecolor(_DISCORD_DARK)
            ax.set_facecolor(_DISCORD_DARK)

            x_nums = mdates.date2num(timestamps)
            ax.plot(x_nums, counts, color=_DISCORD_BLURPLE, linewidth=2)
            ax.fill_between(x_nums, counts, alpha=0.2, color=_DISCORD_BLURPLE)

            from humanitz_bot.utils.i18n import t

            ax.set_title(t("chart.title"), color="white", fontsize=14)
            ax.set_ylabel(t("chart.ylabel"), color="white")
            ax.tick_params(colors="white")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))

            for spine in ax.spines.values():
                spine.set_color("#555555")
            ax.grid(axis="y", color="#555555", alpha=0.3)

            fig.autofmt_xdate()
            fig.tight_layout()

            output_path = self._tmp_dir / _CHART_FILENAME
            fig.savefig(output_path, dpi=100, facecolor=fig.get_facecolor())

            return str(output_path)
        except Exception:
            logger.exception("生成圖表失敗")
            return None
        finally:
            plt.close("all")
