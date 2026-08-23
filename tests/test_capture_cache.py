"""Metadatos de captura (dimensiones JPEG)."""

from __future__ import annotations

from nilocardmed.bluetooth.capture_cache import CachedCapture


def test_metadata_includes_resolution():
    cached = CachedCapture(
        capture_id="abc123",
        device_path="/dev/video0",
        capture_path="/data/captures/test.jpg",
        size_bytes=4,
        data=b"\xff\xd8\xff\xd9",
        chunk_size=200,
        width=1280,
        height=720,
    )
    meta = cached.metadata()
    assert meta["width"] == 1280
    assert meta["height"] == 720
    assert meta["resolution"] == "1280x720"
