"""Backends de captura de imagen."""

from __future__ import annotations

import logging
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from nilocardmed.camera.exceptions import CameraBusyError, CameraCaptureError, CameraNotFoundError
from nilocardmed.config.models import CameraSettings

logger = logging.getLogger(__name__)


class CaptureBackend(ABC):
    """Interfaz común para capturar imágenes JPEG desde V4L2."""

    name: str

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def capture(self, device_path: Path, output_path: Path, settings: CameraSettings) -> None:
        raise NotImplementedError

    def _run(self, command: list[str], *, settings: CameraSettings) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=settings.capture_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CameraCaptureError(
                f"Timeout ({settings.capture_timeout_seconds}s) capturando con {self.name}"
            ) from exc
        except FileNotFoundError as exc:
            raise CameraCaptureError(f"Backend {self.name} no disponible: {exc}") from exc

    @staticmethod
    def _map_process_error(device_path: Path, result: subprocess.CompletedProcess[str]) -> None:
        combined = f"{result.stderr}\n{result.stdout}".lower()
        if "no such file" in combined or "cannot find" in combined:
            raise CameraNotFoundError(f"Cámara no disponible: {device_path}")
        if "busy" in combined or "device or resource busy" in combined:
            raise CameraBusyError(f"Cámara ocupada: {device_path}")
        raise CameraCaptureError(
            f"Fallo al capturar desde {device_path}: {result.stderr.strip() or result.stdout.strip()}"
        )


class FsWebcamBackend(CaptureBackend):
    name = "fswebcam"

    def __init__(self, binary: str = "fswebcam") -> None:
        self.binary = binary

    def is_available(self) -> bool:
        return shutil.which(self.binary) is not None

    def capture(self, device_path: Path, output_path: Path, settings: CameraSettings) -> None:
        resolution = f"{settings.width}x{settings.height}"
        command = [
            self.binary,
            "-d",
            str(device_path),
            "-r",
            resolution,
            "--jpeg",
            str(settings.jpeg_quality),
            "--no-banner",
            "--skip",
            str(settings.warmup_frames),
            "-S",
            str(max(settings.warmup_seconds, 0)),
            str(output_path),
        ]
        logger.debug("Ejecutando %s", " ".join(command))
        result = self._run(command, settings=settings)
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            self._map_process_error(device_path, result)


class FfmpegBackend(CaptureBackend):
    name = "ffmpeg"

    def __init__(self, binary: str = "ffmpeg") -> None:
        self.binary = binary

    def is_available(self) -> bool:
        return shutil.which(self.binary) is not None

    def capture(self, device_path: Path, output_path: Path, settings: CameraSettings) -> None:
        # q:v 2 (mejor) .. 31 (peor); mapeamos calidad JPEG 1-100 a rango ffmpeg.
        q_value = max(2, min(31, int(31 - (settings.jpeg_quality / 100) * 29)))
        command = [
            self.binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "v4l2",
            "-input_format",
            settings.input_format,
            "-video_size",
            f"{settings.width}x{settings.height}",
            "-i",
            str(device_path),
            "-frames:v",
            "1",
            "-q:v",
            str(q_value),
            "-y",
            str(output_path),
        ]
        logger.debug("Ejecutando %s", " ".join(command))
        result = self._run(command, settings=settings)
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            self._map_process_error(device_path, result)


def build_backends(settings: CameraSettings) -> list[CaptureBackend]:
    """Construye la lista de backends según configuración."""
    candidates: list[CaptureBackend] = []
    if settings.backend in ("auto", "fswebcam"):
        candidates.append(FsWebcamBackend(settings.fswebcam_binary))
    if settings.backend in ("auto", "ffmpeg"):
        candidates.append(FfmpegBackend(settings.ffmpeg_binary))
    return candidates


def select_backend(settings: CameraSettings) -> CaptureBackend:
    """Selecciona el primer backend disponible."""
    available = [backend for backend in build_backends(settings) if backend.is_available()]
    if not available:
        requested = settings.backend
        raise CameraCaptureError(f"No hay backend de captura disponible (solicitado: {requested})")
    chosen = available[0]
    logger.debug("Backend seleccionado: %s", chosen.name)
    return chosen
