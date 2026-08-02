"""Servicio CardMed: configuración y prueba end-to-end."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nilocardmed.camera.exceptions import CameraError
from nilocardmed.camera.service import CameraService
from nilocardmed.cardmed.exceptions import (
    CardMedConfigError,
    CardMedValidationError,
)
from nilocardmed.cardmed.models import ConfigureResult, TestResult, TestStep
from nilocardmed.cardmed.validation import (
    extract_cardmed_patch,
    validate_capture,
    validate_cardmed_patch,
)
from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import AppConfig, CardMedSettings, EnvironmentSettings
from nilocardmed.ser_client.client import SerClient
from nilocardmed.ser_client.exceptions import SerUploadError
from nilocardmed.ser_client.models import SamplePayload
from nilocardmed.wifi.exceptions import WifiError
from nilocardmed.wifi.service import WifiService

logger = logging.getLogger(__name__)


class CardMedService:
    """Orquesta configuración CardMed y prueba captura → validación → SER."""

    def __init__(
        self,
        config_manager: ConfigManager,
        env: EnvironmentSettings,
    ) -> None:
        self._config_manager = config_manager
        self._env = env

    def get_config(self) -> dict[str, Any]:
        config = self._config_manager.get()
        return self._public_cardmed_dict(config)

    def configure(self, payload: dict[str, Any]) -> ConfigureResult:
        patch = extract_cardmed_patch(payload)
        validate_cardmed_patch(patch)

        config = self._config_manager.get()
        warnings: list[str] = []

        updated = config.cardmed.model_copy(update=patch)
        config.cardmed = updated

        ser_device_id: str | None = config.ser.device_id
        if updated.sync_device_id_to_ser:
            new_id = updated.site_id or updated.device_label
            if new_id:
                config.ser.device_id = str(new_id)
                ser_device_id = config.ser.device_id
            elif patch.get("sync_device_id_to_ser") is True:
                warnings.append("sync_device_id_to_ser activo pero site_id/device_label vacíos")

        if updated.enabled and updated.test_upload_enabled and not config.ser.enabled:
            warnings.append("test_upload_enabled=true pero ser.enabled=false")

        self._config_manager.save(config)
        logger.info("Configuración CardMed actualizada site_id=%s", updated.site_id)

        return ConfigureResult(
            cardmed=self._public_cardmed_dict(config)["cardmed"],
            ser_device_id=ser_device_id,
            warnings=warnings,
        )

    def run_test(
        self,
        *,
        device_path: str | None = None,
        dry_run: bool | None = None,
        skip_upload: bool | None = None,
    ) -> TestResult:
        config = self._config_manager.get()
        cardmed = config.cardmed
        steps: list[TestStep] = []

        if not cardmed.enabled:
            steps.append(TestStep("cardmed_enabled", False, "CardMed deshabilitado"))
            return TestResult(success=False, steps=steps, error="cardmed_disabled")

        steps.append(TestStep("cardmed_enabled", True, "CardMed activo"))

        do_dry_run = cardmed.test_dry_run_default if dry_run is None else dry_run
        do_skip_upload = bool(skip_upload)

        wifi_service = WifiService(config.wifi, config_manager=self._config_manager)

        if cardmed.test_require_wifi:
            try:
                status = wifi_service.status(check_connectivity=False)
            except WifiError as exc:
                steps.append(TestStep("wifi_connected", False, str(exc)))
                return TestResult(success=False, steps=steps, error="wifi_error")
            if not status.connected:
                steps.append(TestStep("wifi_connected", False, "WiFi no conectado"))
                return TestResult(success=False, steps=steps, error="wifi_not_connected")
            steps.append(
                TestStep(
                    "wifi_connected",
                    True,
                    f"Conectado a {status.ssid}",
                    data={"ssid": status.ssid, "ip_address": status.ip_address},
                )
            )
        else:
            steps.append(TestStep("wifi_connected", True, "Comprobación omitida"))

        if cardmed.test_require_connectivity:
            try:
                ok = wifi_service.test_connectivity()
            except WifiError as exc:
                steps.append(TestStep("connectivity", False, str(exc)))
                return TestResult(success=False, steps=steps, error="connectivity_error")
            if not ok:
                steps.append(TestStep("connectivity", False, "Sin conectividad externa"))
                return TestResult(
                    success=False,
                    steps=steps,
                    error="connectivity_failed",
                )
            steps.append(TestStep("connectivity", True, "Conectividad OK"))
        else:
            steps.append(TestStep("connectivity", True, "Comprobación omitida"))

        camera_service = CameraService(config.camera, data_dir=self._env.data_dir)
        try:
            capture = camera_service.capture(device_path=device_path)
        except CameraError as exc:
            steps.append(TestStep("capture", False, str(exc)))
            return TestResult(success=False, steps=steps, error="capture_failed")

        capture_info = {
            "device_path": str(capture.device_path),
            "output_path": str(capture.output_path),
            "backend": capture.backend,
            "width": capture.width,
            "height": capture.height,
            "size_bytes": capture.size_bytes,
            "captured_at": capture.captured_at.isoformat(),
        }
        steps.append(TestStep("capture", True, "Captura OK", data=capture_info))

        try:
            validate_capture(capture, cardmed)
        except CardMedValidationError as exc:
            steps.append(TestStep("validate_image", False, str(exc)))
            return TestResult(
                success=False,
                steps=steps,
                capture=capture_info,
                error="validation_failed",
            )

        steps.append(TestStep("validate_image", True, "Imagen válida"))

        should_upload = (
            cardmed.test_upload_enabled
            and config.ser.enabled
            and not do_dry_run
            and not do_skip_upload
        )

        upload_dict: dict[str, Any] | None = None
        if not should_upload:
            reason = "dry_run" if do_dry_run else "skip_upload" if do_skip_upload else "upload_disabled"
            steps.append(TestStep("upload", True, f"Envío omitido ({reason})"))
            return TestResult(success=True, steps=steps, capture=capture_info, upload=None)

        metadata = self._build_sample_metadata(config)
        try:
            payload = SamplePayload(
                image_bytes=capture.output_path.read_bytes(),
                filename=capture.output_path.name,
                captured_at=capture.captured_at,
                device_id=config.ser.device_id,
                metadata=metadata,
            )
            upload_result = SerClient(config.ser).upload_sample(payload)
            upload_dict = upload_result.to_dict()
        except SerUploadError as exc:
            steps.append(TestStep("upload", False, str(exc)))
            return TestResult(
                success=False,
                steps=steps,
                capture=capture_info,
                error="upload_failed",
            )

        if not upload_result.success:
            steps.append(
                TestStep(
                    "upload",
                    False,
                    upload_result.error or f"HTTP {upload_result.status_code}",
                    data=upload_dict,
                )
            )
            return TestResult(
                success=False,
                steps=steps,
                capture=capture_info,
                upload=upload_dict,
                error="upload_failed",
            )

        steps.append(
            TestStep(
                "upload",
                True,
                f"Enviado a SER ({upload_result.status_code})",
                data={"sample_ref": upload_result.sample_ref},
            )
        )

        if cardmed.test_delete_capture_after_success:
            self._delete_capture(capture.output_path)

        return TestResult(
            success=True,
            steps=steps,
            capture=capture_info,
            upload=upload_dict,
        )

    def _build_sample_metadata(self, config: AppConfig) -> dict[str, Any]:
        cardmed = config.cardmed
        metadata: dict[str, Any] = {
            "source": "cardmed_test",
            "cardmed_enabled": cardmed.enabled,
        }
        if cardmed.site_id:
            metadata["site_id"] = cardmed.site_id
        if cardmed.device_label:
            metadata["device_label"] = cardmed.device_label
        if cardmed.location:
            metadata["location"] = cardmed.location
        if cardmed.operator_id:
            metadata["operator_id"] = cardmed.operator_id
        metadata.update(cardmed.metadata)
        metadata.update(cardmed.extra)
        return metadata

    def _public_cardmed_dict(self, config: AppConfig) -> dict[str, Any]:
        return {
            "cardmed": config.cardmed.model_dump(),
            "ser_device_id": config.ser.device_id,
            "ser_enabled": config.ser.enabled,
            "ser_url": config.ser.url,
        }

    @staticmethod
    def _delete_capture(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("No se pudo eliminar captura de prueba %s: %s", path, exc)
