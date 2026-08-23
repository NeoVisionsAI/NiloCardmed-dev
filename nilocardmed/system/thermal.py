"""Lectura de temperatura del SoC (Raspberry Pi / thermal_zone)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def collect_cpu_temperature(
    *,
    thermal_root: Path | None = None,
    zone_name: str = "cpu-thermal",
) -> dict[str, Any]:
    """
    Lee temperatura desde sysfs (millidegrees Celsius en thermal_zone*).

    En Raspberry Pi suele existir ``/sys/class/thermal/thermal_zone0/temp``.
    """
    root = thermal_root or Path("/sys/class/thermal")
    if not root.is_dir():
        return {"available": False, "celsius": None, "source": None}

    zones: list[Path] = []
    preferred = root / zone_name
    if preferred.is_dir():
        zones.append(preferred)
    zones.extend(
        entry
        for entry in sorted(root.glob("thermal_zone*"))
        if entry.is_dir() and entry not in zones
    )

    for zone in zones:
        temp_path = zone / "temp"
        if not temp_path.is_file():
            continue
        try:
            raw = temp_path.read_text(encoding="utf-8").strip()
            milli = int(raw)
        except (OSError, ValueError):
            continue
        type_path = zone / "type"
        source = type_path.read_text(encoding="utf-8").strip() if type_path.is_file() else zone.name
        celsius = round(milli / 1000.0, 1)
        return {
            "available": True,
            "celsius": celsius,
            "millidegrees": milli,
            "source": source,
            "zone": zone.name,
        }

    return {"available": False, "celsius": None, "source": None}
