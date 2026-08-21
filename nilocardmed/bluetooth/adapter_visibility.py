"""Mantiene el adaptador BlueZ en discoverable/pairable (24/7)."""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

_SHOW_FIELD_RE = re.compile(r"^\s*([A-Za-z][A-Za-z ]*):\s*(.+)\s*$")


def _parse_bluetooth_show(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = _SHOW_FIELD_RE.match(line)
        if not match:
            continue
        key = match.group(1).strip().lower().replace(" ", "_")
        fields[key] = match.group(2).strip()
    return fields


def read_adapter_state(*, timeout: float = 5.0) -> dict[str, str]:
    """Lee estado del adaptador por defecto vía ``bluetoothctl show``."""
    try:
        result = subprocess.run(
            ["bluetoothctl", "show"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("No se pudo leer bluetoothctl show: %s", exc)
        return {}

    if result.returncode != 0 and not result.stdout.strip():
        logger.debug("bluetoothctl show falló: %s", result.stderr.strip())
        return {}

    return _parse_bluetooth_show(result.stdout)


def _run_bluetoothctl(*args: str, timeout: float = 5.0) -> bool:
    try:
        result = subprocess.run(
            ["bluetoothctl", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("bluetoothctl %s: %s", " ".join(args), exc)
        return False
    return result.returncode == 0


def _set_discoverable_via_bluezero(adapter_address: str | None) -> None:
    if not adapter_address:
        return
    try:
        from bluezero import adapter as bz_adapter
    except ImportError:
        return

    try:
        dongle = bz_adapter.Adapter(adapter_address)
        if not dongle.powered:
            dongle.powered = True
        dongle.discoverable = True
    except Exception as exc:
        logger.debug("bluezero discoverable (%s): %s", adapter_address, exc)


def ensure_adapter_visibility(
    *,
    adapter_address: str | None = None,
    require_powered: bool = True,
) -> dict[str, Any]:
    """Garantiza Powered/Discoverable/Pairable; reactiva si BlueZ los apagó.

    Returns:
        dict con ``ok``, ``changed``, ``state`` (antes/después).
    """
    before = read_adapter_state()
    changed = False

    if require_powered and before.get("powered") != "yes":
        if _run_bluetoothctl("power", "on"):
            changed = True
            logger.info("Adaptador Bluetooth encendido (estaba Powered=no)")

    if before.get("discoverable") != "yes":
        if _run_bluetoothctl("discoverable", "on"):
            changed = True
            logger.info("BlueZ discoverable reactivado (estaba off)")

    if before.get("pairable") != "yes":
        if _run_bluetoothctl("pairable", "on"):
            changed = True
            logger.info("BlueZ pairable reactivado (estaba off)")

    _set_discoverable_via_bluezero(adapter_address)

    after = read_adapter_state()
    ok = after.get("discoverable") == "yes" and after.get("pairable") == "yes"
    if require_powered:
        ok = ok and after.get("powered") == "yes"

    if changed and ok:
        from nilocardmed.operations_log import trace_system

        trace_system(
            event="bluetooth_discoverable_restored",
            detail="Discoverable/pairable restaurados",
            discoverable=after.get("discoverable"),
            pairable=after.get("pairable"),
        )

    return {"ok": ok, "changed": changed, "before": before, "after": after}
