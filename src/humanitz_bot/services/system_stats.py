"""系統資源監控服務 — CPU、記憶體、硬碟、網路、開機時間。"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

import psutil

logger = logging.getLogger("humanitz_bot.services.system_stats")

_BYTES_PER_GB = 1024**3

_last_net: dict[str, float] = {}
_net_lock = threading.Lock()


@dataclass
class SystemStats:
    cpu_percent: float
    memory_used: float
    memory_total: float
    memory_percent: float
    disk_used: float
    disk_total: float
    disk_percent: float
    net_sent_per_sec: float
    net_recv_per_sec: float
    uptime_seconds: float


def get_system_stats() -> SystemStats:
    """取得當前系統資源狀態。

    網路速度透過兩次呼叫間的 delta 計算，首次呼叫回傳 0.0。
    """
    cpu = psutil.cpu_percent(interval=0.5)

    mem = psutil.virtual_memory()
    memory_used = mem.used / _BYTES_PER_GB
    memory_total = mem.total / _BYTES_PER_GB

    disk = psutil.disk_usage(os.path.abspath(os.sep))
    disk_used = disk.used / _BYTES_PER_GB
    disk_total = disk.total / _BYTES_PER_GB

    net = psutil.net_io_counters()
    now = time.monotonic()
    sent_per_sec = 0.0
    recv_per_sec = 0.0

    with _net_lock:
        if _last_net:
            elapsed = now - _last_net["time"]
            if elapsed > 0:
                sent_per_sec = max(0.0, (net.bytes_sent - _last_net["sent"]) / elapsed)
                recv_per_sec = max(0.0, (net.bytes_recv - _last_net["recv"]) / elapsed)

        _last_net["time"] = now
        _last_net["sent"] = net.bytes_sent
        _last_net["recv"] = net.bytes_recv

    uptime = time.time() - psutil.boot_time()

    return SystemStats(
        cpu_percent=cpu,
        memory_used=round(memory_used, 2),
        memory_total=round(memory_total, 2),
        memory_percent=mem.percent,
        disk_used=round(disk_used, 2),
        disk_total=round(disk_total, 2),
        disk_percent=disk.percent,
        net_sent_per_sec=round(sent_per_sec, 2),
        net_recv_per_sec=round(recv_per_sec, 2),
        uptime_seconds=round(uptime, 2),
    )
