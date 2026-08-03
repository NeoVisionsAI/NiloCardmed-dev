"""Persistencia de secretos fuera de config.json."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, SecretStr

from nilocardmed.config.models import AppConfig

logger = logging.getLogger(__name__)

SECRETS_FILENAME = "secrets.json"

# Rutas anidadas en config -> clave en secrets.json
SECRET_PATHS: tuple[tuple[str, ...], ...] = (
    ("bluetooth", "password"),
    ("wifi", "password"),
    ("ser", "api_key"),
    ("ser", "basic_password"),
)


def secrets_path(data_dir: Path) -> Path:
    return data_dir / SECRETS_FILENAME


def _get_nested(model: BaseModel, path: tuple[str, ...]) -> Any:
    current: Any = model
    for part in path:
        current = getattr(current, part)
    return current


def _set_nested(model: BaseModel, path: tuple[str, ...], value: Any) -> None:
    parent = model
    for part in path[:-1]:
        parent = getattr(parent, part)
    setattr(parent, path[-1], value)


def extract_secrets(config: AppConfig) -> dict[str, str]:
    """Extrae secretos con valor no vacío."""
    secrets: dict[str, str] = {}
    for path in SECRET_PATHS:
        value = _get_nested(config, path)
        if isinstance(value, SecretStr):
            text = value.get_secret_value()
            if text:
                secrets[".".join(path)] = text
        elif isinstance(value, str) and value:
            secrets[".".join(path)] = value
    return secrets


def strip_secrets_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Elimina secretos del dict listo para config.json."""
    result = _deep_copy_dict(payload)
    for path in SECRET_PATHS:
        key = ".".join(path)
        _delete_nested_key(result, path)
        if key in result:
            del result[key]
    return result


def _delete_nested_key(data: dict[str, Any], path: tuple[str, ...]) -> None:
    node = data
    for part in path[:-1]:
        if not isinstance(node, dict) or part not in node:
            return
        node = node[part]
    if isinstance(node, dict) and path[-1] in node:
        node[path[-1]] = None


def _deep_copy_dict(data: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(data))


def load_secrets_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("No se pudo leer %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if value}


def save_secrets_file(path: Path, secrets: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_secrets_file(path)
    existing.update({key: value for key, value in secrets.items() if value})
    cleaned = {key: value for key, value in existing.items() if value}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    logger.debug("Secretos guardados en %s", path)


def apply_secrets_to_config(config: AppConfig, secrets: dict[str, str]) -> AppConfig:
    for path in SECRET_PATHS:
        key = ".".join(path)
        if key not in secrets:
            continue
        value = secrets[key]
        field = _get_nested(config, path)
        if isinstance(field, SecretStr):
            _set_nested(config, path, SecretStr(value))
        else:
            _set_nested(config, path, value)
    return config


def merge_secrets_into_raw(raw: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
    """Fusiona secretos en el dict cargado antes de validar AppConfig."""
    merged = _deep_copy_dict(raw)
    for path in SECRET_PATHS:
        key = ".".join(path)
        if key not in secrets:
            continue
        node: Any = merged
        for part in path[:-1]:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        node[path[-1]] = secrets[key]
    return merged
