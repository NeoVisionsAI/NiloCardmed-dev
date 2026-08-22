"""Tests del servidor HTTP de aprovisionamiento WiFi."""

from __future__ import annotations

import json
import random
import threading
from collections.abc import Callable
from http.client import HTTPConnection
from unittest.mock import MagicMock

from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import AppConfig, BluetoothSettings, EnvironmentSettings, HttpSettings
from nilocardmed.http.server import create_http_server


def _free_port() -> int:
    return random.randint(30000, 60000)


def _make_server(port: int | None = None):
    bind_port = port if port is not None else _free_port()
    env = EnvironmentSettings(
        data_dir="/tmp/nilocardmed-test-http",
        bluetooth=BluetoothSettings(enabled=False, password="secret"),
        http=HttpSettings(
            enabled=True,
            port=bind_port,
            bind_ap_only=False,
            host="127.0.0.1",
        ),
    )
    config = AppConfig()
    manager = MagicMock(spec=ConfigManager)
    manager.load.return_value = config
    server = create_http_server(env.http, manager, env, env.bluetooth)
    return server, bind_port


def _with_server(exercise: Callable[[int], None]) -> None:
    server, bound_port = _make_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        exercise(bound_port)
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_api_status():
    def _run(port: int) -> None:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/status")
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert body["status"] == "ok"
        assert body["device"] == "Nilocardmed"

    _with_server(_run)


def test_api_command_auth():
    def _run(port: int) -> None:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        payload = json.dumps({"cmd": "auth", "payload": {"password": "secret"}})
        conn.request(
            "POST",
            "/api/command",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert body["ok"] is True
        assert "token" in body["data"]

    _with_server(_run)


def test_cors_preflight():
    def _run(port: int) -> None:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("OPTIONS", "/api/status")
        response = conn.getresponse()
        assert response.status == 204
        assert response.getheader("Access-Control-Allow-Origin") == "*"

    _with_server(_run)
