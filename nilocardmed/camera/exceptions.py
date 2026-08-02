"""Excepciones del módulo de cámara."""


class CameraError(Exception):
    """Error base relacionado con cámaras."""


class CameraNotFoundError(CameraError):
    """No se encontró la cámara solicitada."""


class CameraBusyError(CameraError):
    """La cámara está en uso por otro proceso."""


class CameraCaptureError(CameraError):
    """Fallo al capturar una imagen."""
