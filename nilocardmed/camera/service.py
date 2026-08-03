"""Servicio de alto nivel para listar y capturar desde cámaras USB."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from nilocardmed.camera.backends import select_backend
from nilocardmed.camera.discovery import list_cameras, resolve_device
from nilocardmed.camera.exceptions import CameraError
from nilocardmed.camera.models import CameraDevice, CaptureResult
from nilocardmed.camera.processing import resize_jpeg_if_needed
from nilocardmed.camera.validation import validate_jpeg_file
from nilocardmed.config.models import CameraSettings

logger = logging.getLogger(__name__)


class CameraService:
    """Orquesta descubrimiento y captura usando configuración parametrizable."""

    def __init__(self, settings: CameraSettings, *, data_dir: Path | None = None) -> None:
        self.settings = settings
        self.data_dir = data_dir or Path("/data")

    @property
    def capture_dir(self) -> Path:
        if self.settings.capture_dir:
            base = Path(self.settings.capture_dir)
        else:
            base = self.data_dir / "captures"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def list_cameras(self, *, include_non_capture: bool | None = None) -> list[CameraDevice]:
        include = (
            self.settings.include_non_capture
            if include_non_capture is None
            else include_non_capture
        )
        return list_cameras(
            device_glob=self.settings.device_glob,
            v4l2_ctl_binary=self.settings.v4l2_ctl_binary,
            discovery_timeout_seconds=self.settings.discovery_timeout_seconds,
            include_non_capture=include,
        )

    def capture(
        self,
        *,
        device_path: str | None = None,
        output_path: Path | None = None,
        backend: str | None = None,
    ) -> CaptureResult:
        """Captura una imagen JPEG validada; reintenta si el fichero es corrupto."""
        last_error: Exception | None = None
        attempts = self.settings.capture_max_attempts

        for attempt in range(1, attempts + 1):
            target = output_path or self._default_output_path(
                resolve_device(
                    device_path or self.settings.device_path,
                    device_glob=self.settings.device_glob,
                    v4l2_ctl_binary=self.settings.v4l2_ctl_binary,
                    discovery_timeout_seconds=self.settings.discovery_timeout_seconds,
                ).path
            )
            try:
                return self._capture_once(
                    device_path=device_path,
                    output_path=target,
                    backend=backend,
                )
            except CameraError as exc:
                last_error = exc
                logger.warning(
                    "Captura inválida (intento %s/%s): %s",
                    attempt,
                    attempts,
                    exc,
                )
                if target.exists():
                    try:
                        target.unlink()
                    except OSError:
                        pass
                if attempt < attempts:
                    time.sleep(self.settings.capture_retry_delay_seconds)

        raise CameraError(
            f"Captura fallida tras {attempts} intentos: {last_error}"
        ) from last_error

    def _capture_once(
        self,
        *,
        device_path: str | None,
        output_path: Path,
        backend: str | None,
    ) -> CaptureResult:
        device = resolve_device(
            device_path or self.settings.device_path,
            device_glob=self.settings.device_glob,
            v4l2_ctl_binary=self.settings.v4l2_ctl_binary,
            discovery_timeout_seconds=self.settings.discovery_timeout_seconds,
        )

        if not device.supports_capture:
            raise CameraError(f"El dispositivo {device.path} no soporta captura de vídeo")

        settings = self.settings.model_copy(deep=True)
        if backend:
            settings = settings.model_copy(update={"backend": backend})

        target = output_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()

        capture_backend = select_backend(settings)
        logger.info(
            "Capturando imagen desde %s con backend %s -> %s",
            device.path,
            capture_backend.name,
            target,
        )
        capture_backend.capture(device.path, target, settings)

        validate_jpeg_file(target, settings)
        width, height, _ = resize_jpeg_if_needed(target, settings)
        validate_jpeg_file(target, settings)

        result = CaptureResult.from_file(
            device_path=device.path,
            output_path=target,
            backend=capture_backend.name,
            width=width,
            height=height,
        )
        logger.info(
            "Captura OK (%s bytes) backend=%s device=%s",
            result.size_bytes,
            result.backend,
            result.device_path,
        )
        return result

    def _default_output_path(self, device_path: Path) -> Path:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
        filename = f"{device_path.name}_{timestamp}_{uuid4().hex[:8]}.jpg"
        return self.capture_dir / filename
