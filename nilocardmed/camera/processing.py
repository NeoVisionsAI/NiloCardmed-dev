"""Post-procesado de imágenes capturadas (reescalado JPEG)."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from nilocardmed.camera.exceptions import CameraError
from nilocardmed.config.models import CameraSettings

logger = logging.getLogger(__name__)


def get_jpeg_dimensions(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise CameraError(
            "Pillow no instalado; necesario para reescalado (pip install Pillow)"
        ) from exc

    with Image.open(path) as image:
        return image.size


def resize_jpeg_if_needed(path: Path, settings: CameraSettings) -> tuple[int, int, bool]:
    """
    Reescala el JPEG in-place si supera las dimensiones objetivo.

    Returns:
        (width, height, resized)
    """
    if not settings.resize_after_capture:
        width, height = get_jpeg_dimensions(path)
        return width, height, False

    target_w = settings.output_width
    target_h = settings.output_height
    current_w, current_h = get_jpeg_dimensions(path)

    needs_resize = current_w > target_w or current_h > target_h
    if settings.resize_only_if_larger and not needs_resize:
        return current_w, current_h, False

    if current_w == target_w and current_h == target_h:
        return current_w, current_h, False

    try:
        from PIL import Image
    except ImportError as exc:
        raise CameraError("Pillow no instalado") from exc

    quality = settings.jpeg_quality_after_resize or settings.jpeg_quality
    with Image.open(path) as image:
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        resized = image.resize((target_w, target_h), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        resized.save(buffer, format="JPEG", quality=quality, optimize=True)
        path.write_bytes(buffer.getvalue())

    logger.info(
        "Imagen reescalada %dx%d -> %dx%d (%s)",
        current_w,
        current_h,
        target_w,
        target_h,
        path.name,
    )
    return target_w, target_h, True
