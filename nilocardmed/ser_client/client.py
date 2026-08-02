"""Cliente HTTP configurable hacia la API REST de SER."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from nilocardmed.config.models import SerSettings
from nilocardmed.ser_client.exceptions import SerConfigError, SerUploadError
from nilocardmed.ser_client.models import SamplePayload, UploadResult
from nilocardmed.ser_client.payload import build_metadata, build_request

logger = logging.getLogger(__name__)


class SerClient:
    """Envía muestras (imágenes) a SER con reintentos y logging estructurado."""

    def __init__(self, settings: SerSettings) -> None:
        self.settings = settings

    def upload_sample(self, payload: SamplePayload) -> UploadResult:
        """Envía una muestra a SER aplicando reintentos con backoff."""
        if not self.settings.enabled:
            raise SerConfigError("Cliente SER deshabilitado (ser.enabled=false)")

        method, httpx_kwargs, headers, params, _ = build_request(self.settings, payload)
        max_attempts = self.settings.max_retries + 1
        backoff = self.settings.retry_backoff_seconds
        started = time.perf_counter()

        last_result: UploadResult | None = None
        last_error: str | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                result = self._send_once(
                    method=method,
                    httpx_kwargs=httpx_kwargs,
                    headers=headers,
                    params=params,
                    attempt=attempt,
                    started=started,
                )
                last_result = result

                if result.success:
                    logger.info(
                        "Muestra enviada a SER status=%s attempts=%s elapsed_ms=%.2f url=%s sample_ref=%s",
                        result.status_code,
                        result.attempts,
                        result.elapsed_ms,
                        result.url,
                        result.sample_ref,
                    )
                    return result

                if not self._should_retry(result.status_code, attempt, max_attempts):
                    break

            except httpx.RequestError as exc:
                last_error = str(exc)
                logger.warning(
                    "Error de red enviando a SER (attempt %s/%s): %s",
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt >= max_attempts:
                    break

            if attempt < max_attempts:
                logger.info(
                    "Reintento SER en %.2fs (attempt %s/%s)",
                    backoff,
                    attempt,
                    max_attempts,
                )
                time.sleep(backoff)
                backoff = min(
                    backoff * self.settings.retry_backoff_multiplier,
                    self.settings.retry_max_backoff_seconds,
                )

        if last_result is not None:
            logger.error(
                "Fallo enviando muestra a SER status=%s attempts=%s error=%s",
                last_result.status_code,
                last_result.attempts,
                last_result.error,
            )
            raise SerUploadError(last_result.error or "Envío a SER fallido")

        elapsed_ms = (time.perf_counter() - started) * 1000
        raise SerUploadError(
            last_error or f"Envío a SER fallido tras {max_attempts} intentos "
            f"(elapsed_ms={elapsed_ms:.2f})"
        )

    def upload_file(
        self,
        image_path: Path,
        *,
        metadata: dict[str, Any] | None = None,
        device_id: str | None = None,
    ) -> UploadResult:
        """Carga una imagen desde disco y la envía a SER."""
        path = Path(image_path)
        if not path.is_file():
            raise SerUploadError(f"Imagen no encontrada: {path}")

        payload = SamplePayload(
            image_bytes=path.read_bytes(),
            filename=path.name,
            device_id=device_id,
            metadata=metadata or {},
        )
        return self.upload_sample(payload)

    def _send_once(
        self,
        *,
        method: str,
        httpx_kwargs: dict[str, Any],
        headers: dict[str, str],
        params: dict[str, str] | None,
        attempt: int,
        started: float,
    ) -> UploadResult:
        with httpx.Client(
            timeout=self.settings.timeout_seconds,
            verify=self.settings.verify_ssl,
        ) as client:
            response = client.request(
                method,
                self.settings.url,
                headers=headers,
                params=params,
                **httpx_kwargs,
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        body_text = response.text[: self.settings.max_response_body_log_chars]
        response_json = None
        sample_ref = None

        content_type = response.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    response_json = parsed
                    for key in self.settings.sample_id_response_fields:
                        if key in parsed:
                            sample_ref = str(parsed[key])
                            break
            except ValueError:
                response_json = None

        success = response.status_code in self.settings.success_status_codes
        error = None if success else f"HTTP {response.status_code}: {body_text[:200]}"

        result = UploadResult(
            success=success,
            status_code=response.status_code,
            attempts=attempt,
            elapsed_ms=elapsed_ms,
            url=str(response.request.url),
            response_body=body_text if body_text else None,
            response_json=response_json,
            error=error,
            sample_ref=sample_ref,
        )

        if not success:
            logger.warning(
                "SER respondió con error status=%s attempt=%s body=%s",
                response.status_code,
                attempt,
                body_text[:200],
            )

        return result

    def _should_retry(
        self,
        status_code: int | None,
        attempt: int,
        max_attempts: int,
    ) -> bool:
        if attempt >= max_attempts:
            return False
        if status_code is None:
            return True
        return status_code in self.settings.retry_on_status_codes

    def dry_run(self, payload: SamplePayload) -> dict[str, Any]:
        """Describe la petición que se enviaría, sin ejecutarla."""
        method, httpx_kwargs, headers, params, raw_body = build_request(
            self.settings, payload
        )
        redact = {name.lower() for name in self.settings.redact_header_names}
        safe_headers = {
            key: ("***" if key.lower() in redact else value) for key, value in headers.items()
        }
        summary: dict[str, Any] = {
            "enabled": self.settings.enabled,
            "method": method,
            "url": self.settings.url,
            "params": params,
            "headers": safe_headers,
            "payload_mode": self.settings.payload_mode,
            "metadata_keys": list(build_metadata(self.settings, payload).keys()),
            "image_bytes": len(payload.image_bytes),
            "filename": payload.filename,
        }
        if "json" in httpx_kwargs:
            json_body = dict(httpx_kwargs["json"])
            if self.settings.json_image_field in json_body:
                json_body[self.settings.json_image_field] = (
                    f"<base64 {len(payload.image_bytes)} bytes>"
                )
            summary["json_body"] = json_body
        if "files" in httpx_kwargs:
            summary["multipart_files"] = list(httpx_kwargs["files"].keys())
            summary["multipart_data"] = httpx_kwargs.get("data")
        if raw_body is not None:
            summary["raw_body_bytes"] = len(raw_body)
        return summary
