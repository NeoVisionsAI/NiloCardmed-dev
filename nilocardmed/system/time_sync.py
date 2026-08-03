"""Sincronización de hora del sistema (p. ej. desde tablet vía BLE)."""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime
from shutil import which

logger = logging.getLogger(__name__)


class TimeSyncError(Exception):
    """No se pudo ajustar la hora del sistema."""


def set_system_time(
    *,
    epoch: float | None = None,
    iso8601: str | None = None,
) -> dict:
    """Establece la hora del sistema. Requiere CAP_SYS_TIME o root."""
    if epoch is not None:
        target = datetime.fromtimestamp(epoch, tz=UTC)
    elif iso8601:
        normalized = iso8601.replace("Z", "+00:00")
        target = datetime.fromisoformat(normalized)
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
    else:
        raise TimeSyncError("Indica epoch o iso8601")

    epoch_value = target.timestamp()
    errors: list[str] = []

    if _try_timedatectl(epoch_value):
        logger.info("Hora sincronizada vía timedatectl -> %s", target.isoformat())
        return {
            "ok": True,
            "method": "timedatectl",
            "time": target.isoformat(),
            "epoch": epoch_value,
        }

    if _try_date_command(epoch_value):
        logger.info("Hora sincronizada vía date -> %s", target.isoformat())
        return {
            "ok": True,
            "method": "date",
            "time": target.isoformat(),
            "epoch": epoch_value,
        }

    raise TimeSyncError(
        "Sin permisos para fijar hora (añade cap SYS_TIME al contenedor o ejecuta en host)"
    )


def _try_timedatectl(epoch: float) -> bool:
    if not which("timedatectl"):
        return False
    try:
        subprocess.run(
            ["timedatectl", "set-time", datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("timedatectl falló: %s", exc)
        return False


def _try_date_command(epoch: float) -> bool:
    if not which("date"):
        return False
    try:
        subprocess.run(
            ["date", "-s", f"@{int(epoch)}"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return True
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("date falló: %s", exc)
        return False


def read_system_time() -> dict:
    now = datetime.now(tz=UTC)
    return {"epoch": now.timestamp(), "iso8601": now.isoformat()}
