"""Lectura de batería / alimentación desde sysfs (Linux power_supply)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_supply(path: Path) -> dict[str, Any]:
    name = path.name
    supply_type = _read_text(path / "type")
    status = _read_text(path / "status")
    capacity = _read_int(path / "capacity")
    energy_full = _read_int(path / "energy_full")
    energy_now = _read_int(path / "energy_now")
    voltage = _read_int(path / "voltage_now")
    online = _read_int(path / "online")

    entry: dict[str, Any] = {
        "name": name,
        "type": supply_type,
        "status": status,
        "capacity_percent": capacity,
        "online": bool(online) if online is not None else None,
    }
    if energy_now is not None and energy_full:
        entry["energy_ratio"] = round(energy_now / energy_full, 4)
    if voltage is not None:
        entry["voltage_uv"] = voltage
    return entry


def collect_battery_status(*, power_supply_root: Path | None = None) -> dict[str, Any]:
    """
    Devuelve fuentes de energía del kernel.

    En Pi alimentada por USB/powerbank suele no haber entrada Battery;
    en HAT UPS/PiJuice aparecerá en /sys/class/power_supply/.
    """
    root = power_supply_root or Path("/sys/class/power_supply")
    if not root.is_dir():
        return {
            "available": False,
            "message": "No hay /sys/class/power_supply en este sistema",
            "sources": [],
            "primary": None,
        }

    sources = [_read_supply(entry) for entry in sorted(root.iterdir()) if entry.is_dir()]
    battery_sources = [s for s in sources if (s.get("type") or "").lower() == "battery"]
    primary = None
    if battery_sources:
        primary = max(
            battery_sources,
            key=lambda item: item.get("capacity_percent") if item.get("capacity_percent") is not None else -1,
        )
    elif sources:
        primary = sources[0]

    available = any(s.get("capacity_percent") is not None for s in sources)
    message = None
    if not available:
        message = (
            "Sin métrica de batería en el kernel (normal con alimentación USB directa o powerbank sin datos)"
        )

    result: dict[str, Any] = {
        "available": available,
        "sources": sources,
        "primary": primary,
    }
    if message:
        result["message"] = message
    if primary and primary.get("capacity_percent") is not None:
        result["level_percent"] = primary["capacity_percent"]
        result["status"] = primary.get("status")
    return result
