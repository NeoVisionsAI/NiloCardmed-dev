"""Validación de integridad de imágenes JPEG capturadas."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from nilocardmed.camera.exceptions import CameraError
from nilocardmed.config.models import CameraSettings

logger = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_jpeg_file(
    path: Path,
    settings: CameraSettings,
    *,
    min_bytes: int | None = None,
) -> tuple[int, int]:
    """
    Comprueba que el JPEG existe, tiene tamaño mínimo y se puede decodificar.

    Returns:
        (width, height)

    Raises:
        CameraError: si el fichero es inválido o corrupto.
    """
    if not path.exists():
        raise CameraError(f"Captura no encontrada: {path}")

    size = path.stat().st_size
    floor = min_bytes if min_bytes is not None else settings.capture_min_bytes
    if size < floor:
        raise CameraError(f"JPEG demasiado pequeño ({size}B < {floor}B): {path}")

    header = path.read_bytes()[:3]
    if header != b"\xff\xd8\xff":
        raise CameraError(f"Cabecera JPEG inválida en {path}")

    try:
        from PIL import Image
    except ImportError as exc:
        raise CameraError("Pillow no instalado; necesario para validar JPEG") from exc

    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except Exception as exc:
        raise CameraError(f"JPEG corrupto o ilegible: {path}") from exc

    if width < 1 or height < 1:
        raise CameraError(f"Dimensiones JPEG inválidas: {width}x{height}")

    return width, height
