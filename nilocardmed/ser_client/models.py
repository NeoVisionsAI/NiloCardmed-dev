"""Modelos del cliente SER."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class UploadResult:
    """Resultado del envío de una muestra a SER."""

    success: bool
    status_code: int | None
    attempts: int
    elapsed_ms: float
    url: str
    response_body: str | None = None
    response_json: dict[str, Any] | None = None
    error: str | None = None
    sample_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status_code": self.status_code,
            "attempts": self.attempts,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "url": self.url,
            "response_body": self.response_body,
            "response_json": self.response_json,
            "error": self.error,
            "sample_ref": self.sample_ref,
        }


@dataclass(slots=True)
class SamplePayload:
    """Datos de una muestra lista para enviar."""

    image_bytes: bytes
    filename: str
    content_type: str = "image/jpeg"
    captured_at: datetime | None = None
    device_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
