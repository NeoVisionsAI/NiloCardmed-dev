"""Agregado de estado del dispositivo para el panel de aprovisionamiento WiFi."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nilocardmed import __version__
from nilocardmed.bluetooth.protocol import CommandContext
from nilocardmed.camera.exceptions import CameraError
from nilocardmed.camera.service import CameraService
from nilocardmed.config.manager import ConfigManager
from nilocardmed.sampler.window import evaluate_window
from nilocardmed.storage.manager import StorageManager
from nilocardmed.system.power import collect_battery_status
from nilocardmed.system.thermal import collect_cpu_temperature
from nilocardmed.telemetry.store import telemetry
from nilocardmed.wifi.exceptions import WifiError
from nilocardmed.wifi.service import WifiService


def config_last_saved_at(config_manager: ConfigManager) -> str | None:
    path = config_manager.config_path
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=UTC).isoformat()


def capture_statistics(ctx: CommandContext) -> dict[str, Any]:
    config = ctx.config_manager.get()
    storage = StorageManager(
        config.storage,
        ctx.env,
        captures_dir=_captures_dir(config, ctx),
    )
    disk = storage.disk_status()
    cycle_stats = telemetry.capture_stats()

    return {
        "images_on_disk": disk.get("captures_count", 0),
        "pending_uploads": disk.get("pending_count", 0),
        "cycles_recorded": cycle_stats["cycles_recorded"],
        "cycles_successful": cycle_stats["cycles_successful"],
        "last_capture_success_at": _epoch_to_iso(cycle_stats.get("last_success_at_epoch")),
        "last_sampler_tick_at": _epoch_to_iso(cycle_stats.get("last_sampler_tick_at_epoch")),
    }


def camera_status(ctx: CommandContext) -> dict[str, Any]:
    config = ctx.config_manager.get()
    saved_device = config.camera.device_path
    service = CameraService(config.camera, data_dir=ctx.env.data_dir)

    try:
        cameras = service.list_cameras(include_non_capture=False)
    except CameraError as exc:
        return {
            "connected": False,
            "saved_device": saved_device,
            "cameras_count": 0,
            "cameras": [],
            "error": str(exc),
        }

    camera_items = [
        {
            "id": device.id,
            "path": str(device.path),
            "name": device.name,
            "driver": device.driver,
            "supports_capture": device.supports_capture,
        }
        for device in cameras
    ]
    saved_present = bool(
        saved_device and any(str(device.path) == saved_device for device in cameras)
    )
    active_device = saved_device if saved_present else (
        str(cameras[0].path) if len(cameras) == 1 else None
    )

    return {
        "connected": len(cameras) > 0,
        "saved_device": saved_device,
        "saved_device_present": saved_present,
        "active_device": active_device,
        "cameras_count": len(cameras),
        "cameras": camera_items,
    }


def build_device_status(ctx: CommandContext) -> dict[str, Any]:
    config = ctx.config_manager.get()
    wifi_service = WifiService(config.wifi, config_manager=ctx.config_manager)

    window = evaluate_window(config.sampling)
    sampling = {
        "enabled": config.sampling.enabled,
        "interval_seconds": config.sampling.interval_seconds,
        "monitor_start": config.sampling.monitor_start,
        "monitor_end": config.sampling.monitor_end,
        "window_active": window.active,
        "window_phase": window.phase.value,
    }

    try:
        wifi = wifi_service.status(check_connectivity=False).to_dict()
    except WifiError as exc:
        wifi = {
            "connected": False,
            "ssid": config.wifi.ssid,
            "error": str(exc),
        }

    return {
        "device_name": config.bluetooth.device_name,
        "version": __version__,
        "wifi": wifi,
        "power": collect_battery_status(),
        "system": collect_cpu_temperature(),
        "sampling": sampling,
        "camera": camera_status(ctx),
        "captures": capture_statistics(ctx),
        "cardmed": {
            "enabled": config.cardmed.enabled,
            "site_id": config.cardmed.site_id,
            "device_label": config.cardmed.device_label,
            "operator_id": config.cardmed.operator_id,
            "configured": bool(config.cardmed.site_id or config.cardmed.device_label),
        },
        "config_last_saved_at": config_last_saved_at(ctx.config_manager),
        "refreshed_at": datetime.now(tz=UTC).isoformat(),
    }


def _captures_dir(config, ctx: CommandContext) -> Path:
    if config.camera.capture_dir:
        return Path(config.camera.capture_dir)
    return ctx.env.data_dir / "captures"


def _epoch_to_iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()
