"""Información del sistema y actualizaciones."""

from __future__ import annotations

import os
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from nilocardmed import __version__
from nilocardmed.config.models import AppConfig, EnvironmentSettings
from nilocardmed.storage.manager import StorageManager
from nilocardmed.telemetry.store import telemetry


def collect_system_info(
    config: AppConfig,
    env: EnvironmentSettings,
    *,
    sampler_running: bool | None = None,
) -> dict[str, Any]:
    storage = StorageManager(config.storage, env)
    usage = shutil.disk_usage(env.data_dir)
    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "hostname": os.environ.get("HOSTNAME") or platform.node(),
        "uptime_seconds": round(telemetry.uptime_seconds(), 1),
        "started_at_epoch": telemetry.started_at_epoch,
        "last_success_at_epoch": telemetry.last_success_at_epoch,
        "data_dir": str(env.data_dir),
        "config_path": str(env.config_path),
        "log_dir": str(env.log_dir) if env.log_dir else None,
        "image": os.environ.get("NILOCARDMED_IMAGE"),
        "sampler_running": sampler_running,
        "disk": storage.disk_status(),
        "memory": _memory_info(),
        "update": {
            "install_dir": os.environ.get("NILOCARDMED_INSTALL_DIR", "/opt/nilocardmed"),
            "hint": "En Pi: cd $INSTALL_DIR && git pull && sudo ./scripts/install.sh",
        },
    }


def _memory_info() -> dict[str, Any]:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return {"available_mb": None}
    available_kb = None
    for line in meminfo.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            available_kb = int(line.split()[1])
            break
    return {"available_mb": (available_kb // 1024) if available_kb else None}
