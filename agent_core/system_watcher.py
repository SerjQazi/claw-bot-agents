"""Background system watcher for AgentOS."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
import os
import socket
import time
from typing import Any

import psutil


class SystemWatcher:
    name = "system_watcher"

    def __init__(self, interval_seconds: int = 60, max_logs: int = 200) -> None:
        self.interval_seconds = interval_seconds
        self._logs: deque[dict[str, Any]] = deque(maxlen=max_logs)
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_heartbeat: str | None = None
        self._hostname = socket.gethostname()

    async def start(self) -> dict:
        if self._running and self._task and not self._task.done():
            return self.status()

        self._running = True
        self._log("info", "system_watcher started")
        self.check_once()
        self._task = asyncio.create_task(self._run(), name="system_watcher")
        return self.status()

    async def stop(self) -> dict:
        self._running = False
        task = self._task
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._task = None
        self._log("info", "system_watcher stopped")
        return self.status()

    def status(self) -> dict:
        return {
            "agent": self.name,
            "running": self._running and self._task is not None and not self._task.done(),
            "state": "running" if self._running else "stopped",
            "last_heartbeat": self._last_heartbeat,
            "interval_seconds": self.interval_seconds,
            "log_count": len(self._logs),
        }

    def recent_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, len(self._logs) or 1))
        return list(self._logs)[-safe_limit:]

    async def _run(self) -> None:
        while self._running:
            await asyncio.sleep(self.interval_seconds)
            if not self._running:
                break
            try:
                self.check_once()
            except Exception as error:  # pragma: no cover - defensive background guard
                self._log("error", f"system_watcher error: {error}")

    def check_once(self) -> dict:
        boot_time = psutil.boot_time()
        uptime_seconds = int(time.time() - boot_time)
        cpu_percent = psutil.cpu_percent(interval=None)
        memory_percent = psutil.virtual_memory().percent
        disk_percent = psutil.disk_usage("/").percent
        load_avg = self._load_average()
        cpu_count = psutil.cpu_count(logical=True) or 1

        self._last_heartbeat = self._now()
        self._log(
            "info",
            f"system_watcher heartbeat: {self._hostname}",
            {
                "cpu_percent": cpu_percent,
                "memory_percent": memory_percent,
                "disk_percent": disk_percent,
                "load_avg": load_avg,
                "uptime": self._format_uptime(uptime_seconds),
            },
        )

        if cpu_percent > 85:
            self._log("warning", f"WARNING: CPU usage high: {self._format_percent(cpu_percent)}")
        if memory_percent > 85:
            self._log("warning", f"WARNING: RAM usage high: {self._format_percent(memory_percent)}")
        if disk_percent > 90:
            self._log("warning", f"WARNING: disk usage high: {self._format_percent(disk_percent)}")
        if load_avg and load_avg["1m"] > cpu_count:
            self._log("warning", f"WARNING: load average high: {load_avg['1m']} > {cpu_count} cores")

        return self.status()

    def _log(self, level: str, message: str, data: dict | None = None) -> None:
        self._logs.append(
            {
                "timestamp": self._now(),
                "source": self.name,
                "level": level,
                "message": message,
                "data": data or {},
            }
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _format_uptime(seconds: int) -> str:
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{days}d {hours}h {minutes}m"

    @staticmethod
    def _format_percent(value: float) -> str:
        return f"{value:.0f}%" if float(value).is_integer() else f"{value:.1f}%"

    @staticmethod
    def _load_average() -> dict | None:
        if not hasattr(os, "getloadavg"):
            return None
        one, five, fifteen = os.getloadavg()
        return {
            "1m": round(one, 2),
            "5m": round(five, 2),
            "15m": round(fifteen, 2),
        }
