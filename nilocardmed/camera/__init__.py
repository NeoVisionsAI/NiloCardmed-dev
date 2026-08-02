"""Detección y captura de cámaras USB."""

from nilocardmed.camera.discovery import list_cameras, resolve_device
from nilocardmed.camera.exceptions import (
    CameraBusyError,
    CameraCaptureError,
    CameraError,
    CameraNotFoundError,
)
from nilocardmed.camera.models import CameraDevice, CaptureResult
from nilocardmed.camera.service import CameraService

__all__ = [
    "CameraBusyError",
    "CameraCaptureError",
    "CameraDevice",
    "CameraError",
    "CameraNotFoundError",
    "CameraService",
    "CaptureResult",
    "list_cameras",
    "resolve_device",
]
