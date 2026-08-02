"""Modelos del motor de muestreo."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nilocardmed.ser_client.models import UploadResult


@dataclass(slots=True)
class SampleCycleResult:
    """Resultado de un ciclo captura (+ envío opcional)."""

    success: bool
    capture_path: str | None = None
    capture_backend: str | None = None
    captured_at: datetime | None = None
    upload: UploadResult | None = None
    capture_error: str | None = None
    upload_error: str | None = None
    skipped_upload: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "capture_path": self.capture_path,
            "capture_backend": self.capture_backend,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "upload": self.upload.to_dict() if self.upload else None,
            "capture_error": self.capture_error,
            "upload_error": self.upload_error,
            "skipped_upload": self.skipped_upload,
        }


@dataclass(slots=True)
class SamplerState:
    """Estado en memoria del motor de muestreo."""

    running: bool = False
    cycles_total: int = 0
    cycles_success: int = 0
    cycles_failed: int = 0
    consecutive_failures: int = 0
    last_cycle: SampleCycleResult | None = None
    stop_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "cycles_total": self.cycles_total,
            "cycles_success": self.cycles_success,
            "cycles_failed": self.cycles_failed,
            "consecutive_failures": self.consecutive_failures,
            "last_cycle": self.last_cycle.to_dict() if self.last_cycle else None,
            "stop_reason": self.stop_reason,
        }
