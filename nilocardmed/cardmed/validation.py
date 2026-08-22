"""Validaciones de configuración y captura CardMed."""

from __future__ import annotations

from nilocardmed.camera.models import CaptureResult
from nilocardmed.cardmed.exceptions import CardMedConfigError, CardMedValidationError
from nilocardmed.config.models import CardMedSettings

CARDMED_CONFIG_FIELDS = frozenset(
    {
        "enabled",
        "site_id",
        "device_label",
        "location",
        "operator_id",
        "sync_device_id_to_ser",
        "metadata",
        "extra",
        "test_upload_enabled",
        "test_require_wifi",
        "test_require_connectivity",
        "test_min_image_bytes",
        "test_min_width",
        "test_min_height",
        "test_delete_capture_after_success",
        "test_dry_run_default",
    }
)


def extract_cardmed_patch(payload: dict) -> dict:
    """Extrae campos CardMed de un payload Bluetooth/CLI."""
    if "config_code" in payload and payload["config_code"] is not None:
        from nilocardmed.cardmed.config_codes import parse_config_code

        return parse_config_code(str(payload["config_code"]))

    if "config_json" in payload and payload["config_json"] is not None:
        raw = payload["config_json"]
        if isinstance(raw, dict):
            nested = raw
        else:
            import json

            try:
                nested = json.loads(str(raw))
            except json.JSONDecodeError as exc:
                raise CardMedConfigError(f"config_json inválido: {exc}") from exc
        if not isinstance(nested, dict):
            raise CardMedConfigError("config_json debe ser un objeto JSON")
        if "cardmed" in nested and isinstance(nested["cardmed"], dict):
            return dict(nested["cardmed"])
        return {key: value for key, value in nested.items() if key in CARDMED_CONFIG_FIELDS}

    if "cardmed" in payload and isinstance(payload["cardmed"], dict):
        return dict(payload["cardmed"])
    return {key: value for key, value in payload.items() if key in CARDMED_CONFIG_FIELDS}


def validate_cardmed_patch(patch: dict) -> None:
    """Valida un parcial de configuración CardMed."""
    if not patch:
        raise CardMedConfigError("No se recibieron campos CardMed para configurar")

    for key in ("site_id", "device_label", "location", "operator_id"):
        if key in patch:
            value = patch[key]
            if value is not None and not str(value).strip():
                raise CardMedConfigError(f"{key} no puede estar vacío")
            if value is not None and len(str(value)) > 256:
                raise CardMedConfigError(f"{key} excede 256 caracteres")

    for key in ("metadata", "extra"):
        if key in patch and not isinstance(patch[key], dict):
            raise CardMedConfigError(f"{key} debe ser un objeto JSON")

    for key in ("test_min_image_bytes", "test_min_width", "test_min_height"):
        if key in patch:
            value = int(patch[key])
            if value < 0:
                raise CardMedConfigError(f"{key} debe ser >= 0")


def validate_capture(capture: CaptureResult, settings: CardMedSettings) -> None:
    """Valida que la captura cumple los criterios de prueba CardMed."""
    if capture.size_bytes < settings.test_min_image_bytes:
        raise CardMedValidationError(
            f"Imagen demasiado pequeña ({capture.size_bytes}B < {settings.test_min_image_bytes}B)"
        )
    if capture.width < settings.test_min_width:
        raise CardMedValidationError(
            f"Ancho insuficiente ({capture.width} < {settings.test_min_width})"
        )
    if capture.height < settings.test_min_height:
        raise CardMedValidationError(
            f"Alto insuficiente ({capture.height} < {settings.test_min_height})"
        )

    header = capture.output_path.read_bytes()[:3]
    if header != b"\xff\xd8\xff":
        raise CardMedValidationError("La captura no es un JPEG válido")
