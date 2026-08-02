"""Configuración de logging estructurado."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class StructuredFormatter(logging.Formatter):
    """Formateador JSON para logs parseables en producción."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _build_formatter(structured: bool) -> logging.Formatter:
    if structured:
        return StructuredFormatter()
    return logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def setup_logging(
    level: str = "INFO",
    structured: bool = True,
    log_dir: Path | None = None,
    log_filename: str = "nilocardmed.log",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """Configura el root logger de la aplicación."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    formatter = _build_formatter(structured)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    root.addHandler(stdout_handler)

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_dir / log_filename,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "No se pudo escribir en %s: %s; solo stdout", log_dir, exc
            )

    logging.getLogger("nilocardmed").setLevel(level.upper())
