"""Modelos del módulo de cámara."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

CameraBackendName = Literal["auto", "fswebcam", "ffmpeg"]


@dataclass(frozen=True, slots=True)
class CameraDevice:
    """Dispositivo de vídeo detectado en el sistema."""

    path: Path
    name: str | None = None
    driver: str | None = None
    bus_info: str | None = None
    supports_capture: bool = True

    @property
    def id(self) -> str:
        return self.path.name


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Resultado de una captura de imagen."""

    device_path: Path
    output_path: Path
    backend: str
    width: int
    height: int
    size_bytes: int
    captured_at: datetime

    @property
    def data(self) -> bytes:
        return self.output_path.read_bytes()

    def to_dict(self) -> dict:
        return {
            "device_path": str(self.device_path),
            "output_path": str(self.output_path),
            "backend": self.backend,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "captured_at": self.captured_at.isoformat(),
        }

    @classmethod
    def from_file(
        cls,
        *,
        device_path: Path,
        output_path: Path,
        backend: str,
        width: int,
        height: int,
    ) -> CaptureResult:
        stat = output_path.stat()
        return cls(
            device_path=device_path,
            output_path=output_path,
            backend=backend,
            width=width,
            height=height,
            size_bytes=stat.st_size,
            captured_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        )
