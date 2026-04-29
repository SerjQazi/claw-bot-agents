"""Safe self-healing monitor for AgentOS."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
import os
import subprocess
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import psutil


class SelfHealingAgent:
    name = "self_healing_agent"
    description = "Monitors AgentOS and safely suggests recovery actions."
    ollama_tags_url = "http://127.0.0.1:11434/api/tags"

    def __init__(self, interval_seconds: int = 60, max_logs: int = 200) -> None:
        self.interval_seconds = interval_seconds
        self._logs: deque[dict[str, Any]] = deque(maxlen=max_logs)
        self._suggestions: deque[dict[str, Any]] = deque(maxlen=50)
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_check: str | None = None

    async def start(self) -> dict:
        if self._running and self._task and not self._task.done():
            return self.status()

        self._running = True
        self._log("info", "self_healing_agent started")
        self.check_once()
        self._task = asyncio.create_task(self._run(), name=self.name)
        return self.status()

    async def stop(self) -> dict:
        self._running = False
        task = self._task
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._task = None
        self._log("info", "self_healing_agent stopped")
        return self.status()

    def status(self) -> dict:
        return {
            "agent": self.name,
            "running": self._running and self._task is not None and not self._task.done(),
            "state": "running" if self._running else "stopped",
            "last_check": self._last_check,
            "interval_seconds": self.interval_seconds,
            "suggestion_count": len(self._suggestions),
            "log_count": len(self._logs),
        }

    def suggestions(self) -> list[dict[str, Any]]:
        return list(self._suggestions)

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
                self._log("error", f"self_healing_agent error: {error}")

    def check_once(self) -> dict:
        self._last_check = self._now()
        self._suggestions.clear()

        self._check_agentos_health()
        self._check_service_health("agentos", "restart_agentos")
        self._check_service_health("ollama", "restart_ollama")
        self._check_ollama_health()
        self._check_resource_pressure()

        self._log("info", "self_healing_agent heartbeat")
        return self.status()

    def approve(self, action: str) -> dict:
        allowed_commands = {
            "restart_ollama": ["sudo", "/bin/systemctl", "restart", "ollama"],
            "restart_agentos": ["sudo", "/bin/systemctl", "restart", "agentos"],
        }
        command = allowed_commands.get(action)
        if command is None:
            return {
                "agent": self.name,
                "action": action,
                "stdout": "",
                "stderr": f"Unsupported self-heal action: {action}",
                "exit_code": 2,
            }

        try:
            self._log("info", f"executing approved sudo action: {action}", {"command": command})
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            response = {
                "agent": self.name,
                "action": action,
                "command": command,
                "stdout": error.stdout or "",
                "stderr": (error.stderr or "") + "\nCommand timed out.",
                "exit_code": 124,
            }
            self._log("error", f"self-heal action {action} timed out", response)
            return response

        response = {
            "agent": self.name,
            "action": action,
            "command": command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
        level = "info" if result.returncode == 0 else "error"
        self._log(level, f"self-heal action {action} exited with code {result.returncode}", response)
        return response

    def _check_agentos_health(self) -> None:
        try:
            status = self.status()
            if status.get("agent") != self.name:
                raise RuntimeError("unexpected agent status")
        except Exception as error:
            self._warn("AgentOS health check failed", "restart_agentos", str(error))

    def _check_service_health(self, service_name: str, action: str) -> None:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", service_name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError:
            self._log("info", f"systemctl unavailable; skipped {service_name} service health check")
            return
        except subprocess.TimeoutExpired:
            self._warn(f"{service_name} service health check timed out", action, "systemctl is-active timed out")
            return

        state = result.stdout.strip() or result.stderr.strip() or "unknown"
        if result.returncode != 0 or state != "active":
            self._warn(f"{service_name} service health check failed: {state}", action, result.stderr.strip())

    def _check_ollama_health(self) -> None:
        request = Request(self.ollama_tags_url, method="GET")
        try:
            with urlopen(request, timeout=2) as response:
                if response.status >= 400:
                    self._warn(
                        f"Ollama API returned HTTP {response.status}",
                        "restart_ollama",
                        self.ollama_tags_url,
                    )
        except (OSError, URLError) as error:
            self._warn("Ollama API unreachable", "restart_ollama", str(error))

    def _check_resource_pressure(self) -> None:
        cpu_percent = psutil.cpu_percent(interval=None)
        memory_percent = psutil.virtual_memory().percent
        disk_percent = psutil.disk_usage("/").percent
        load_avg = self._load_average()
        cpu_count = psutil.cpu_count(logical=True) or 1
        uptime = self._format_uptime(int(time.time() - psutil.boot_time()))

        if cpu_percent > 85:
            self._warn(f"CPU usage high: {self._format_percent(cpu_percent)}", None, "reduce load")
        if memory_percent > 85:
            self._warn(f"RAM usage high: {self._format_percent(memory_percent)}", None, "inspect memory pressure")
        if disk_percent > 90:
            self._warn(f"Disk usage high: {self._format_percent(disk_percent)}", None, "free disk space")
        if load_avg and load_avg["1m"] > cpu_count:
            self._warn(f"Load average high: {load_avg['1m']} > {cpu_count} cores", None, "inspect running processes")

        self._log(
            "info",
            "self_healing_agent resource check completed",
            {
                "cpu_percent": cpu_percent,
                "memory_percent": memory_percent,
                "disk_percent": disk_percent,
                "load_avg": load_avg,
                "uptime": uptime,
            },
        )

    def _warn(self, message: str, action: str | None, detail: str) -> None:
        event = {
            "timestamp": self._now(),
            "source": self.name,
            "level": "warning",
            "message": f"WARNING: {message}",
            "suggested_action": action,
            "detail": detail,
        }
        self._suggestions.append(event)
        self._log("warning", event["message"], event)

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
    def _format_percent(value: float) -> str:
        return f"{value:.0f}%" if float(value).is_integer() else f"{value:.1f}%"

    @staticmethod
    def _format_uptime(seconds: int) -> str:
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{days}d {hours}h {minutes}m"

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
