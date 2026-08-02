"""Caché de la última captura para transferencia por chunks BLE."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass


@dataclass(slots=True)
class CachedCapture:
    """Captura JPEG en memoria para lectura por chunks."""

    capture_id: str
    device_path: str
    capture_path: str
    size_bytes: int
    data: bytes
    chunk_size: int

    @property
    def total_chunks(self) -> int:
        if self.chunk_size <= 0:
            return 0
        return (self.size_bytes + self.chunk_size - 1) // self.chunk_size

    def chunk(self, index: int) -> dict:
        if index < 0 or index >= self.total_chunks:
            raise IndexError("chunk index out of range")
        start = index * self.chunk_size
        end = min(start + self.chunk_size, self.size_bytes)
        payload = self.data[start:end]
        return {
            "capture_id": self.capture_id,
            "index": index,
            "total_chunks": self.total_chunks,
            "chunk_size": len(payload),
            "chunk_base64": base64.b64encode(payload).decode("ascii"),
        }

    def metadata(self) -> dict:
        digest = hashlib.sha256(self.data).hexdigest()
        return {
            "capture_id": self.capture_id,
            "device_path": self.device_path,
            "capture_path": self.capture_path,
            "size_bytes": self.size_bytes,
            "sha256": digest,
            "chunk_size": self.chunk_size,
            "total_chunks": self.total_chunks,
        }


class CaptureCache:
    """Almacén de la última captura."""

    def __init__(self) -> None:
        self._capture: CachedCapture | None = None

    def store(self, capture: CachedCapture) -> None:
        self._capture = capture

    @property
    def current(self) -> CachedCapture | None:
        return self._capture

    def get(self, capture_id: str | None = None) -> CachedCapture:
        if self._capture is None:
            raise LookupError("no_capture_available")
        if capture_id and capture_id != self._capture.capture_id:
            raise LookupError("capture_not_found")
        return self._capture
