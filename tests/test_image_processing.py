"""Tests de reescalado JPEG."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from nilocardmed.camera.processing import get_jpeg_dimensions, resize_jpeg_if_needed
from nilocardmed.config.models import CameraSettings


def _make_jpeg(path: Path, size: tuple[int, int]) -> None:
    image = Image.new("RGB", size, color=(10, 20, 30))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    path.write_bytes(buffer.getvalue())


def test_resize_downscales_large_image(tmp_path: Path):
    path = tmp_path / "large.jpg"
    _make_jpeg(path, (2560, 1440))
    settings = CameraSettings(
        output_width=1280,
        output_height=720,
        resize_after_capture=True,
        resize_only_if_larger=True,
    )

    width, height, resized = resize_jpeg_if_needed(path, settings)

    assert resized is True
    assert (width, height) == (1280, 720)
    assert get_jpeg_dimensions(path) == (1280, 720)


def test_skips_resize_when_already_small(tmp_path: Path):
    path = tmp_path / "small.jpg"
    _make_jpeg(path, (640, 480))
    settings = CameraSettings(
        output_width=1280,
        output_height=720,
        resize_after_capture=True,
        resize_only_if_larger=True,
    )

    width, height, resized = resize_jpeg_if_needed(path, settings)

    assert resized is False
    assert (width, height) == (640, 480)
