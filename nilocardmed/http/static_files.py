"""Carga de la GUI estática de aprovisionamiento WiFi."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from pathlib import Path

_STATIC_PACKAGE = "nilocardmed.http.static"


def static_dir() -> Path:
    """Directorio con index.html y assets/ (desarrollo o instalado)."""
    return Path(__file__).resolve().parent / "static"


@lru_cache(maxsize=16)
def read_static(relative_path: str) -> bytes:
    """Lee un fichero estático (cacheado en memoria)."""
    path = static_dir() / relative_path
    if path.is_file():
        return path.read_bytes()
    try:
        ref = resources.files(_STATIC_PACKAGE).joinpath(relative_path)
        return ref.read_bytes()
    except (FileNotFoundError, TypeError, OSError) as exc:
        raise FileNotFoundError(relative_path) from exc


def provisioning_index_html() -> bytes:
    return read_static("index.html")


def provisioning_mime(path: str) -> str:
    if path.endswith(".css"):
        return "text/css; charset=utf-8"
    if path.endswith(".js"):
        return "application/javascript; charset=utf-8"
    if path.endswith(".html"):
        return "text/html; charset=utf-8"
    return "application/octet-stream"
