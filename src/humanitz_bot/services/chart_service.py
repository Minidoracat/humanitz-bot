from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402

from humanitz_bot.services.database import Database
from humanitz_bot.utils.i18n import t

logger = logging.getLogger("humanitz_bot.services.chart_service")

_DISCORD_DARK = "#2b2d31"
_DISCORD_BLURPLE = "#5865F2"
_CHART_FILENAME = "player_chart.png"


class ChartService:
    def __init__(
        self, db: Database, tmp_dir: str = "tmp", history_hours: int = 24
    ) -> None:
        self._db = db
        self._history_hours = history_hours
        self._tmp_dir = Path(tmp_dir)
        self._tmp_dir.mkdir(parents=True, exist_ok=True)

    def add_data_point(self, player_count: int) -> None:
        self._db.add_player_count(player_count)

    def generate_chart(self) -> str | None:
        rows = self._db.get_player_count_history(self._history_hours)
        if not rows:
            return None

        timestamps: list[datetime] = []
        counts: list[int] = []
        for ts_str, count in rows:
            try:
                timestamps.append(datetime.fromisoformat(ts_str))
                counts.append(count)
            except ValueError:
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
