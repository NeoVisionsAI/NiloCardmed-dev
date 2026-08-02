"""Handlers de comandos Bluetooth (Fase 7)."""

from __future__ import annotations

import base64
import logging
from typing import Any
from uuid import uuid4

from nilocardmed import __version__
from nilocardmed.bluetooth.capture_cache import CachedCapture, CaptureCache
from nilocardmed.bluetooth.command_errors import BluetoothCommandError
from nilocardmed.bluetooth.models import CommandRequest
from nilocardmed.bluetooth.protocol import CommandContext, CommandHandler
from nilocardmed.camera.exceptions import CameraError
from nilocardmed.camera.service import CameraService
from nilocardmed.config.models import AppConfig
from nilocardmed.cardmed.handlers import (
    handle_cardmed_configure,
    handle_cardmed_get,
    handle_cardmed_test,
)
from nilocardmed.resilience.handlers import handle_health_status
from nilocardmed.sampler.window import evaluate_window
from nilocardmed.wifi.exceptions import WifiError
from nilocardmed.wifi.service import WifiService

logger = logging.getLogger(__name__)

REGISTERED_COMMANDS: list[str] = []


def _require_payload_field(request: CommandRequest, field: str) -> Any:
    if field not in request.payload:
        raise BluetoothCommandError("invalid_parameter", f"Falta campo '{field}'")
    return request.payload[field]


def _save_config(ctx: CommandContext, config: AppConfig) -> None:
    ctx.config_manager.save(config)


def handle_auth(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    password = request.payload.get("password")
    expected = ctx.settings.password.get_secret_value()
    if password != expected:
        raise BluetoothCommandError("invalid_password")
    token, expires_in = ctx.sessions.issue_token()
    return {"token": token, "expires_in": expires_in, "device_name": ctx.settings.device_name}


def handle_ping(_ctx: CommandContext, _request: CommandRequest) -> dict[str, Any]:
    return {"pong": True, "version": __version__}


def handle_commands_list(_ctx: CommandContext, _request: CommandRequest) -> dict[str, Any]:
    return {"commands": REGISTERED_COMMANDS}


def _camera_device_to_dict(device) -> dict[str, Any]:
    return {
        "id": device.id,
        "path": str(device.path),
        "name": device.name,
        "driver": device.driver,
        "bus_info": device.bus_info,
        "supports_capture": device.supports_capture,
    }


def handle_camera_list(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    config = ctx.config_manager.get()
    include = request.payload.get("include_non_capture", False)
    service = CameraService(config.camera, data_dir=ctx.env.data_dir)
    try:
        cameras = service.list_cameras(include_non_capture=bool(include))
    except CameraError as exc:
        raise BluetoothCommandError("camera_error", str(exc)) from exc
    return {"cameras": [_camera_device_to_dict(device) for device in cameras]}


def handle_camera_capture_test(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    config = ctx.config_manager.get()
    device = request.payload.get("device")
    mode = request.payload.get("mode", ctx.settings.capture_test_mode)
    service = CameraService(config.camera, data_dir=ctx.env.data_dir)

    try:
        result = service.capture(device_path=device)
        image_data = result.output_path.read_bytes()
    except CameraError as exc:
        raise BluetoothCommandError("camera_error", str(exc)) from exc

    capture_id = uuid4().hex[:12]
    cached = CachedCapture(
        capture_id=capture_id,
        device_path=str(result.device_path),
        capture_path=str(result.output_path),
        size_bytes=result.size_bytes,
        data=image_data,
        chunk_size=ctx.settings.capture_chunk_size,
    )
    ctx.capture_cache.store(cached)

    if mode == "chunked":
        meta = cached.metadata()
        meta["mode"] = "chunked"
        meta["backend"] = result.backend
        return meta

    if mode == "path":
        return {
            "mode": "path",
            "capture_id": capture_id,
            "device_path": str(result.device_path),
            "capture_path": str(result.output_path),
            "size_bytes": result.size_bytes,
            "backend": result.backend,
        }

    if len(image_data) > ctx.settings.max_image_response_bytes:
        raise BluetoothCommandError(
            "response_too_large",
            f"Imagen {result.size_bytes}B; usa mode=chunked o mode=path",
        )

    return {
        "mode": "base64",
        "capture_id": capture_id,
        "device_path": str(result.device_path),
        "size_bytes": result.size_bytes,
        "backend": result.backend,
        "image_base64": base64.b64encode(image_data).decode("ascii"),
    }


def handle_camera_capture_chunk(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    index = int(_require_payload_field(request, "index"))
    capture_id = request.payload.get("capture_id")
    try:
        capture = ctx.capture_cache.get(capture_id)
        return capture.chunk(index)
    except LookupError as exc:
        raise BluetoothCommandError(str(exc)) from exc
    except (IndexError, ValueError) as exc:
        raise BluetoothCommandError("invalid_parameter", str(exc)) from exc


def handle_sampling_get(ctx: CommandContext, _request: CommandRequest) -> dict[str, Any]:
    config = ctx.config_manager.get()
    window = evaluate_window(config.sampling)
    return {
        "sampling": config.sampling.model_dump(),
        "window": window.to_dict(),
    }


def handle_sampling_set_interval(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    interval = int(_require_payload_field(request, "interval_seconds"))
    if interval < 1:
        raise BluetoothCommandError("invalid_parameter", "interval_seconds debe ser >= 1")

    config = ctx.config_manager.get()
    config.sampling.interval_seconds = interval
    _save_config(ctx, config)
    return {"interval_seconds": interval}


def handle_sampling_set_window(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    monitor_start = int(_require_payload_field(request, "monitor_start"))
    monitor_end = int(_require_payload_field(request, "monitor_end"))

    config = ctx.config_manager.get()
    config.sampling.monitor_start = monitor_start
    config.sampling.monitor_end = monitor_end
    _save_config(ctx, config)
    window = evaluate_window(config.sampling)
    return {
        "monitor_start": monitor_start,
        "monitor_end": monitor_end,
        "window": window.to_dict(),
    }


def handle_wifi_scan(ctx: CommandContext, _request: CommandRequest) -> dict[str, Any]:
    config = ctx.config_manager.get()
    service = WifiService(config.wifi, config_manager=ctx.config_manager)
    try:
        networks = service.scan()
    except WifiError as exc:
        raise BluetoothCommandError("wifi_error", str(exc)) from exc
    return {"networks": [network.to_dict() for network in networks]}


def handle_wifi_connect(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    ssid = str(_require_payload_field(request, "ssid"))
    password = request.payload.get("password")
    persist = bool(request.payload.get("persist", True))

    config = ctx.config_manager.get()
    service = WifiService(config.wifi, config_manager=ctx.config_manager)
    try:
        status = service.connect(ssid, password, persist=persist)
    except WifiError as exc:
        raise BluetoothCommandError("wifi_connection_failed", str(exc)) from exc
    return status.to_dict()


def handle_wifi_status(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    check = bool(request.payload.get("check_connectivity", True))
    config = ctx.config_manager.get()
    service = WifiService(config.wifi, config_manager=ctx.config_manager)
    try:
        status = service.status(check_connectivity=check)
    except WifiError as exc:
        raise BluetoothCommandError("wifi_error", str(exc)) from exc
    return status.to_dict()


def handle_wifi_test(ctx: CommandContext, _request: CommandRequest) -> dict[str, Any]:
    config = ctx.config_manager.get()
    service = WifiService(config.wifi, config_manager=ctx.config_manager)
    try:
        ok = service.test_connectivity()
    except WifiError as exc:
        raise BluetoothCommandError("wifi_error", str(exc)) from exc
    return {"connectivity_ok": ok}


def register_operation_handlers(router) -> None:
    """Registra todos los handlers de operaciones."""
    global REGISTERED_COMMANDS

    handler_map: dict[str, CommandHandler] = {}
    definitions: list[tuple[str, CommandHandler, list[str]]] = [
        ("auth", handle_auth, []),
        ("ping", handle_ping, []),
        ("commands_list", handle_commands_list, ["list_commands"]),
        ("camera_list", handle_camera_list, ["list_cameras"]),
        ("camera_capture_test", handle_camera_capture_test, ["capture_test"]),
        ("camera_capture_chunk", handle_camera_capture_chunk, []),
        ("sampling_get", handle_sampling_get, []),
        ("sampling_set_interval", handle_sampling_set_interval, ["set_interval"]),
        ("sampling_set_window", handle_sampling_set_window, ["set_monitor_window"]),
        ("wifi_scan", handle_wifi_scan, []),
        ("wifi_connect", handle_wifi_connect, ["wifi_configure"]),
        ("wifi_status", handle_wifi_status, []),
        ("wifi_test", handle_wifi_test, []),
        ("cardmed_get", handle_cardmed_get, ["get_cardmed_config"]),
        ("cardmed_configure", handle_cardmed_configure, ["configure_cardmed", "configurar"]),
        ("cardmed_test", handle_cardmed_test, ["probar_cardmed", "test_cardmed", "probar"]),
        ("health_status", handle_health_status, ["health", "system_health"]),
    ]

    for name, handler, aliases in definitions:
        handler_map[name] = handler
        for alias in aliases:
            handler_map[alias] = handler

    for command, handler in handler_map.items():
        router.register(command, handler)

    REGISTERED_COMMANDS = sorted(handler_map.keys())
