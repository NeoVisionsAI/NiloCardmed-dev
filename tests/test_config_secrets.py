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


def test_environment_settings_accepts_comma_separated_int_lists(monkeypatch):
    monkeypatch.setenv("NILOCARDMED_SER__SUCCESS_STATUS_CODES", "200,201,202,204")
    monkeypatch.setenv("NILOCARDMED_SER__RETRY_ON_STATUS_CODES", "408,429,500,502,503,504")

    env = EnvironmentSettings()

    assert env.ser.success_status_codes == [200, 201, 202, 204]
    assert env.ser.retry_on_status_codes == [408, 429, 500, 502, 503, 504]


def test_environment_settings_accepts_plain_string_str_list(monkeypatch):
    monkeypatch.setenv("NILOCARDMED_BLUETOOTH__ALLOWED_COMMANDS_WITHOUT_AUTH", "auth")

    env = EnvironmentSettings()

    assert env.bluetooth.allowed_commands_without_auth == ["auth"]


def test_environment_settings_accepts_json_int_lists(monkeypatch):
    monkeypatch.setenv("NILOCARDMED_SER__SUCCESS_STATUS_CODES", "[200,201,202,204]")
    monkeypatch.setenv("NILOCARDMED_SER__RETRY_ON_STATUS_CODES", "[408,429,500,502,503,504]")

    env = EnvironmentSettings()

    assert env.ser.success_status_codes == [200, 201, 202, 204]
    assert env.ser.retry_on_status_codes == [408, 429, 500, 502, 503, 504]


def test_environment_settings_accepts_json_str_list(monkeypatch):
    monkeypatch.setenv("NILOCARDMED_BLUETOOTH__ALLOWED_COMMANDS_WITHOUT_AUTH", '["auth"]')

    env = EnvironmentSettings()

    assert env.bluetooth.allowed_commands_without_auth == ["auth"]
