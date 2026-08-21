"""Tests del protocolo Bluetooth (mock)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from nilocardmed.bluetooth.framing import decode_frames
from nilocardmed.bluetooth.service import BluetoothService
from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import EnvironmentSettings


def _mock_service() -> BluetoothService:
    temp = Path(tempfile.mkdtemp(prefix="nilocardmed-test-"))
    env = EnvironmentSettings().model_copy(update={"data_dir": temp})
    cm = ConfigManager(env)
    cfg = cm.load()
    cfg = cfg.model_copy(
        update={
            "bluetooth": cfg.bluetooth.model_copy(update={"backend": "mock"}),
            "wifi": cfg.wifi.model_copy(update={"backend": "mock"}),
        }
    )
    cm._config = cfg
    return BluetoothService(cfg.bluetooth, cm, env)


def test_auth_and_ping():
    service = _mock_service()
    auth_raw = json.dumps({"cmd": "auth", "password": "changeme"}).encode()
    auth = json.loads(decode_frames(service.process_mock_frames(auth_raw)).decode())
    assert auth["ok"] is True
    token = auth["data"]["token"]

    ping_raw = json.dumps({"cmd": "ping", "token": token}).encode()
    ping = json.loads(decode_frames(service.process_mock_frames(ping_raw)).decode())
    assert ping["ok"] is True
    assert ping["data"]["pong"] is True


def test_health_status_command():
    service = _mock_service()
    auth = json.loads(
        decode_frames(
            service.process_mock_frames(
                json.dumps({"cmd": "auth", "password": "changeme"}).encode()
            )
        ).decode()
    )
    token = auth["data"]["token"]
    resp = json.loads(
        decode_frames(
            service.process_mock_frames(
                json.dumps({"cmd": "health_status", "token": token}).encode()
            )
        ).decode()
    )
    assert resp["ok"] is True
    assert "healthy" in resp["data"]


def test_system_info_and_time_get():
    service = _mock_service()
    auth = json.loads(
        decode_frames(
            service.process_mock_frames(
                json.dumps({"cmd": "auth", "password": "changeme"}).encode()
            )
        ).decode()
    )
    token = auth["data"]["token"]

    def _call(cmd: str, **extra):
        payload = {"cmd": cmd, "token": token, **extra}
        return json.loads(
            decode_frames(
                service.process_mock_frames(json.dumps(payload).encode())
            ).decode()
        )

    info = _call("system_info")
    assert info["ok"] is True
    assert "version" in info["data"]

    storage = _call("storage_status")
    assert storage["ok"] is True
    assert "free_percent" in storage["data"]

    clock = _call("time_get")
    assert clock["ok"] is True
    assert "epoch" in clock["data"]

    history = _call("sampler_history", limit=5)
    assert history["ok"] is True
    assert "cycles" in history["data"]

    events = _call("events_list", limit=5)
    assert events["ok"] is True
    assert "events" in events["data"]

    battery = _call("battery_status")
    assert battery["ok"] is True
    assert "available" in battery["data"]

    commands = _call("commands_list")
    assert commands["ok"] is True
    assert "battery_status" in commands["data"]["commands"]
    assert "camera_test" in commands["data"]["commands"]
    assert "wifi_disconnect" in commands["data"]["commands"]
