"""Tests del panel agregado device_status / dashboard."""

from __future__ import annotations

from unittest.mock import MagicMock

from nilocardmed.bluetooth.protocol import CommandContext
from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import AppConfig, BluetoothSettings, EnvironmentSettings
from nilocardmed.system.device_status import build_device_status


def test_build_device_status_includes_window_phase(tmp_path):
    env = EnvironmentSettings(data_dir=tmp_path)
    manager = ConfigManager(env)
    config = AppConfig()
    manager.save(config)

    ctx = MagicMock(spec=CommandContext)
    ctx.config_manager = manager
    ctx.env = env

    status = build_device_status(ctx)

    assert "window_phase" in status["sampling"]
    assert status["sampling"]["window_phase"] in {"unlimited", "active", "before_start", "after_end"}
    assert "refreshed_at" in status
    assert "power" in status
    assert "system" in status
    assert "camera" in status
