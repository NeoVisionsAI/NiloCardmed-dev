"""Handlers Bluetooth: sistema, telemetría, almacenamiento."""

from __future__ import annotations

from typing import Any

from nilocardmed.bluetooth.command_errors import BluetoothCommandError
from nilocardmed.bluetooth.models import CommandRequest
from nilocardmed.bluetooth.protocol import CommandContext
from nilocardmed.storage.manager import StorageManager
from nilocardmed.system.info import collect_system_info
from nilocardmed.system.power import collect_battery_status
from nilocardmed.system.time_sync import TimeSyncError, read_system_time, set_system_time
from nilocardmed.telemetry.store import telemetry


def handle_system_info(ctx: CommandContext, _request: CommandRequest) -> dict[str, Any]:
    config = ctx.config_manager.get()
    info = collect_system_info(config, ctx.env)
    info["power"] = collect_battery_status()
    return info


def handle_battery_status(_ctx: CommandContext, _request: CommandRequest) -> dict[str, Any]:
    return collect_battery_status()


def handle_storage_status(ctx: CommandContext, _request: CommandRequest) -> dict[str, Any]:
    config = ctx.config_manager.get()
    storage = StorageManager(
        config.storage,
        ctx.env,
        captures_dir=_captures_dir(config, ctx),
    )
    return storage.disk_status()


def handle_sampler_history(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    limit = int(request.payload.get("limit", 20))
    return {"cycles": telemetry.get_cycles(limit=limit)}


def handle_events_list(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    limit = int(request.payload.get("limit", 50))
    return {"events": telemetry.get_events(limit=limit)}


def handle_time_get(_ctx: CommandContext, _request: CommandRequest) -> dict[str, Any]:
    return read_system_time()


def handle_time_sync(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    epoch = request.payload.get("epoch")
    iso8601 = request.payload.get("iso8601")
    try:
        if epoch is not None:
            result = set_system_time(epoch=float(epoch))
        elif iso8601 is not None:
            result = set_system_time(iso8601=str(iso8601))
        else:
            raise BluetoothCommandError("invalid_parameter", "Indica epoch o iso8601")
    except TimeSyncError as exc:
        raise BluetoothCommandError("time_sync_failed", str(exc)) from exc

    telemetry.record_event("time_sync", "Hora sincronizada desde cliente BLE", data=result)
    return result


def _captures_dir(config, ctx: CommandContext):
    if config.camera.capture_dir:
        from pathlib import Path

        return Path(config.camera.capture_dir)
    return ctx.env.data_dir / "captures"
