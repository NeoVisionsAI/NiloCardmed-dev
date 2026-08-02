"""Ventana temporal de monitorización del muestreo."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from nilocardmed.config.models import SamplingSettings


class WindowPhase(str, Enum):
    """Fase actual respecto a la ventana configurada."""

    UNLIMITED = "unlimited"
    ACTIVE = "active"
    BEFORE_START = "before_start"
    AFTER_END = "after_end"


@dataclass(frozen=True, slots=True)
class WindowStatus:
    """Evaluación de la ventana de monitorización en un instante dado."""

    phase: WindowPhase
    active: bool
    now_epoch: float
    monitor_start: int
    monitor_end: int
    seconds_until_start: float | None = None
    seconds_until_end: float | None = None

    def to_dict(self) -> dict:
        return {
            "phase": self.phase.value,
            "active": self.active,
            "now_epoch": self.now_epoch,
            "monitor_start": self.monitor_start,
            "monitor_end": self.monitor_end,
            "seconds_until_start": self.seconds_until_start,
            "seconds_until_end": self.seconds_until_end,
        }


def evaluate_window(
    settings: SamplingSettings,
    *,
    now_epoch: float | None = None,
) -> WindowStatus:
    """Evalúa si el muestreo debe estar activo según monitor_start/monitor_end."""
    now = time.time() if now_epoch is None else now_epoch
    start = settings.monitor_start
    end = settings.monitor_end

    if start == -1 and end == -1:
        return WindowStatus(
            phase=WindowPhase.UNLIMITED,
            active=True,
            now_epoch=now,
            monitor_start=start,
            monitor_end=end,
        )

    if start != -1 and now < start:
        return WindowStatus(
            phase=WindowPhase.BEFORE_START,
            active=False,
            now_epoch=now,
            monitor_start=start,
            monitor_end=end,
            seconds_until_start=start - now,
            seconds_until_end=(end - now) if end != -1 else None,
        )

    if end != -1 and now > end:
        return WindowStatus(
            phase=WindowPhase.AFTER_END,
            active=False,
            now_epoch=now,
            monitor_start=start,
            monitor_end=end,
            seconds_until_end=0.0,
        )

    seconds_until_end = (end - now) if end != -1 else None
    return WindowStatus(
        phase=WindowPhase.ACTIVE,
        active=True,
        now_epoch=now,
        monitor_start=start,
        monitor_end=end,
        seconds_until_end=seconds_until_end,
    )
