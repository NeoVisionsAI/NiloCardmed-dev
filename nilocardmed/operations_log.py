"""Traza operativa: logs Docker + telemetría (BLE, WiFi, config)."""

from __future__ import annotations

import logging
from typing import Any

from nilocardmed.telemetry.store import telemetry

logger = logging.getLogger("nilocardmed.trace")

# Comandos muy frecuentes — solo DEBUG en logs Docker
_QUIET_BLE_COMMANDS = frozenset(
    {
        "ping",
        "camera_capture_chunk",
        "commands_list",
        "list_commands",
        "sampler_history",
        "events_list",
    }
)

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "token",
        "image_base64",
        "api_key",
        "wifi_password",
    }
)


def _redact_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _SENSITIVE_KEYS:
            safe[key] = "***"
        elif isinstance(value, str) and len(value) > 120:
            safe[key] = f"{value[:80]}…({len(value)} chars)"
        else:
            safe[key] = value
    return safe


def trace(
    category: str,
    message: str,
    *,
    data: dict[str, Any] | None = None,
    level: int = logging.INFO,
) -> None:
    """Escribe en logs Docker (nilocardmed.trace) y en telemetry.jsonl."""
    detail = f" [{category}] {message}"
    if data:
        parts = ", ".join(f"{key}={value}" for key, value in data.items())
        detail = f"{detail} ({parts})"
    logger.log(level, detail)
    telemetry.record_event(category, message, data=data)


def trace_ble_command(
    cmd: str,
    *,
    ok: bool,
    error: str | None = None,
    request_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Registra ejecución de comando BLE (sin secretos)."""
    level = logging.INFO
    if cmd in _QUIET_BLE_COMMANDS:
        level = logging.DEBUG

    data: dict[str, Any] = {"cmd": cmd, "ok": ok}
    if request_id:
        data["id"] = request_id
    if error:
        data["error"] = error
    if payload and level == logging.INFO:
        data["payload"] = _redact_payload(payload)

    status = "OK" if ok else "FAIL"
    message = f"comando BLE {cmd} {status}"
    trace("ble", message, data=data, level=level)


def trace_ble_client(*, event: str, detail: str | None = None) -> None:
    """Cliente tablet conectado/desconectado al GATT."""
    data = {"event": event}
    if detail:
        data["detail"] = detail
    trace("ble", f"cliente BLE {event}", data=data)


def trace_wifi(*, event: str, ssid: str | None = None, ip: str | None = None) -> None:
    data: dict[str, Any] = {"event": event}
    if ssid:
        data["ssid"] = ssid
    if ip:
        data["ip"] = ip
    trace("wifi", event, data=data)


def trace_config(*, change: str, **fields: Any) -> None:
    """Cambio de configuración persistida."""
    data = {"change": change, **{k: v for k, v in fields.items() if v is not None}}
    trace("config", change, data=data)


def trace_system(*, event: str, detail: str | None = None, **fields: Any) -> None:
    data: dict[str, Any] = {"event": event, **fields}
    if detail:
        data["detail"] = detail
    trace("system", event, data=data)
