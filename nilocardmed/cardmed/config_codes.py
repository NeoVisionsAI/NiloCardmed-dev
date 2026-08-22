"""Decodificación de QR y códigos de configuración CardMed."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from nilocardmed.cardmed.exceptions import CardMedConfigError
from nilocardmed.cardmed.validation import extract_cardmed_patch, validate_cardmed_patch


def parse_config_code(code: str) -> dict[str, Any]:
    """
    Interpreta un código manual de configuración.

    Formatos aceptados:
    - JSON objeto (campos CardMed o {"cardmed": {...}})
    - Texto pipe: site_id|device_label|operator_id|location
    """
    raw = code.strip()
    if not raw:
        raise CardMedConfigError("El código de configuración está vacío")

    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CardMedConfigError(f"Código JSON inválido: {exc}") from exc
        if not isinstance(payload, dict):
            raise CardMedConfigError("El código JSON debe ser un objeto")
        patch = extract_cardmed_patch(payload)
        validate_cardmed_patch(patch)
        return patch

    parts = [part.strip() for part in raw.split("|")]
    if len(parts) < 1 or not parts[0]:
        raise CardMedConfigError(
            "Formato no reconocido. Usa JSON o site_id|device_label|operator_id|location"
        )

    patch: dict[str, Any] = {"enabled": True}
    field_names = ("site_id", "device_label", "operator_id", "location")
    for index, field in enumerate(field_names):
        if index < len(parts) and parts[index]:
            patch[field] = parts[index]

    validate_cardmed_patch(patch)
    return patch


def decode_qr_from_image(image_path: Path) -> str:
    """Decodifica el primer QR encontrado en una imagen JPEG/PNG."""
    zbarimg = shutil.which("zbarimg")
    if not zbarimg:
        raise CardMedConfigError(
            "zbarimg no está instalado en el dispositivo (paquete zbar-tools)"
        )

    try:
        completed = subprocess.run(
            [zbarimg, "--quiet", "--raw", str(image_path)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CardMedConfigError("Tiempo agotado decodificando QR") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise CardMedConfigError(f"No se detectó QR en la imagen{f': {stderr}' if stderr else ''}")

    payload = completed.stdout.strip()
    if not payload:
        raise CardMedConfigError("QR vacío")
    return payload


def patch_from_qr_payload(payload: str) -> dict[str, Any]:
    """Convierte el contenido de un QR en un patch CardMed."""
    return parse_config_code(payload)
