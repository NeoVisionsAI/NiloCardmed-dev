"""Construcción de peticiones HTTP hacia SER según configuración."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from nilocardmed.config.models import SerSettings
from nilocardmed.ser_client.exceptions import SerConfigError
from nilocardmed.ser_client.models import SamplePayload


def build_metadata(settings: SerSettings, payload: SamplePayload) -> dict[str, Any]:
    """Combina metadatos estáticos, de configuración y de la muestra."""
    metadata: dict[str, Any] = dict(settings.extra_fields)
    metadata.update(payload.metadata)

    if settings.device_id or payload.device_id:
        metadata[settings.device_id_field] = payload.device_id or settings.device_id

    if settings.include_captured_at:
        captured_at = payload.captured_at or datetime.now(tz=UTC)
        metadata[settings.captured_at_field] = captured_at.isoformat()

    return metadata


def build_auth(settings: SerSettings) -> tuple[dict[str, str], dict[str, str]]:
    """Devuelve (headers_extra, query_params) según el tipo de autenticación."""
    headers: dict[str, str] = {}
    params: dict[str, str] = {}

    secret = settings.api_key.get_secret_value() if settings.api_key else None
    if settings.auth_type == "none":
        return headers, params

    if settings.auth_type == "basic":
        username = settings.basic_username or ""
        password = (
            settings.basic_password.get_secret_value() if settings.basic_password else ""
        )
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        headers[settings.auth_header_name] = f"Basic {token}"
        return headers, params

    if not secret:
        raise SerConfigError(
            f"auth_type={settings.auth_type} requiere NILOCARDMED_SER__API_KEY configurada"
        )

    if settings.auth_type == "bearer":
        prefix = settings.auth_header_prefix.strip()
        value = f"{prefix} {secret}".strip() if prefix else secret
        headers[settings.auth_header_name] = value
    elif settings.auth_type == "header":
        prefix = settings.auth_header_prefix.strip()
        value = f"{prefix} {secret}".strip() if prefix else secret
        headers[settings.auth_header_name] = value
    elif settings.auth_type == "query":
        params[settings.auth_query_param] = secret
    else:
        raise SerConfigError(f"Tipo de autenticación no soportado: {settings.auth_type}")

    return headers, params


def build_request(
    settings: SerSettings,
    payload: SamplePayload,
) -> tuple[str, dict[str, Any], dict[str, str], dict[str, str] | None, bytes | None]:
    """
    Construye la petición HTTP.

    Returns:
        (method, kwargs_for_httpx, merged_headers, query_params, raw_body_if_any)
        kwargs puede incluir: json, data, files, content
    """
    metadata = build_metadata(settings, payload)
    auth_headers, query_params = build_auth(settings)
    headers = {**settings.headers, **auth_headers}
    filename = settings.filename or payload.filename

    httpx_kwargs: dict[str, Any] = {}
    raw_body: bytes | None = None

    mode = settings.payload_mode
    if mode == "multipart":
        httpx_kwargs["data"] = {key: _stringify(value) for key, value in metadata.items()}
        httpx_kwargs["files"] = {
            settings.image_field_name: (filename, payload.image_bytes, payload.content_type)
        }
    elif mode == "json_base64":
        body = {
            **metadata,
            settings.json_image_field: base64.b64encode(payload.image_bytes).decode("ascii"),
        }
        httpx_kwargs["json"] = body
        headers.setdefault("Content-Type", "application/json")
    elif mode == "json_base64_data_uri":
        encoded = base64.b64encode(payload.image_bytes).decode("ascii")
        body = {
            **metadata,
            settings.json_image_field: f"data:{payload.content_type};base64,{encoded}",
        }
        httpx_kwargs["json"] = body
        headers.setdefault("Content-Type", "application/json")
    elif mode == "raw_binary":
        raw_body = payload.image_bytes
        headers.setdefault("Content-Type", payload.content_type)
        if settings.content_disposition:
            headers["Content-Disposition"] = settings.content_disposition.format(
                filename=filename
            )
    elif mode == "octet_stream":
        raw_body = payload.image_bytes
        headers.setdefault("Content-Type", "application/octet-stream")
    else:
        raise SerConfigError(f"payload_mode no soportado: {mode}")

    if raw_body is not None:
        httpx_kwargs["content"] = raw_body

    return settings.method, httpx_kwargs, headers, query_params or None, raw_body


def _stringify(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
