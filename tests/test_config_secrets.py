"""Tests de configuración atómica y secretos."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import SecretStr

from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import AppConfig, BluetoothSettings, EnvironmentSettings, WifiSettings
from nilocardmed.config.secrets import secrets_path


def test_save_splits_secrets_and_uses_atomic_config(tmp_path: Path):
    env = EnvironmentSettings().model_copy(update={"data_dir": tmp_path})
    manager = ConfigManager(env)
    config = AppConfig(
        wifi=WifiSettings(ssid="lab", password=SecretStr("wifi-secret")),
        bluetooth=BluetoothSettings(password=SecretStr("bt-secret")),
    )

    manager.save(config)

    raw = json.loads(manager.config_path.read_text(encoding="utf-8"))
    assert raw["wifi"]["password"] is None
    assert raw["bluetooth"]["password"] is None

    secrets = json.loads(secrets_path(tmp_path).read_text(encoding="utf-8"))
    assert secrets["wifi.password"] == "wifi-secret"
    assert secrets["bluetooth.password"] == "bt-secret"

    reloaded = ConfigManager(env).load()
    assert reloaded.wifi.password.get_secret_value() == "wifi-secret"
    assert reloaded.bluetooth.password.get_secret_value() == "bt-secret"
