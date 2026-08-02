"""Carga y persistencia de configuración en disco."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, SecretStr

from nilocardmed.config.models import AppConfig, EnvironmentSettings

logger = logging.getLogger(__name__)


def _dump_for_storage(model: BaseModel) -> dict[str, Any]:
    """Serializa el modelo revelando SecretStr para persistencia local."""
    data: dict[str, Any] = {}
    for key, value in model:
        if isinstance(value, SecretStr):
            data[key] = value.get_secret_value()
        elif isinstance(value, BaseModel):
            data[key] = _dump_for_storage(value)
        else:
            data[key] = value
    return data


class ConfigManager:
    """Gestiona lectura/escritura de config.json con soporte de variables de entorno."""

    def __init__(self, env: EnvironmentSettings | None = None) -> None:
        self._env = env or EnvironmentSettings()
        self._config: AppConfig | None = None

    @property
    def env(self) -> EnvironmentSettings:
        return self._env

    @property
    def config_path(self) -> Path:
        return self._env.config_path

    def load(self) -> AppConfig:
        """Carga configuración desde disco o crea valores por defecto."""
        self._env.data_dir.mkdir(parents=True, exist_ok=True)

        if self.config_path.exists():
            logger.info("Cargando configuración desde %s", self.config_path)
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            config = AppConfig.model_validate(raw)
        else:
            logger.info("No existe configuración previa; usando valores por defecto")
            config = AppConfig()

        config = self._env.apply_to(config)
        self._config = config
        return config

    def save(self, config: AppConfig | None = None) -> None:
        """Persiste la configuración actual en disco."""
        config = config or self._config
        if config is None:
            raise RuntimeError("No hay configuración cargada para guardar")

        self._env.data_dir.mkdir(parents=True, exist_ok=True)
        payload = _dump_for_storage(config)
        self.config_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        self._config = config
        logger.info("Configuración guardada en %s", self.config_path)

    def get(self) -> AppConfig:
        """Devuelve la configuración en memoria, cargándola si es necesario."""
        if self._config is None:
            return self.load()
        return self._config

    def summary(self) -> dict:
        """Resumen legible de la configuración activa (sin secretos)."""
        config = self.get()
        return {
            "ser": {
                "enabled": config.ser.enabled,
                "url": config.ser.url,
                "method": config.ser.method,
                "payload_mode": config.ser.payload_mode,
                "timeout_seconds": config.ser.timeout_seconds,
                "max_retries": config.ser.max_retries,
                "auth_type": config.ser.auth_type,
                "api_key_set": config.ser.api_key is not None,
                "device_id": config.ser.device_id,
            },
            "wifi": {
                "enabled": config.wifi.enabled,
                "ssid": config.wifi.ssid,
                "backend": config.wifi.backend,
                "interface": config.wifi.interface,
                "password_set": config.wifi.password is not None,
                "auto_connect_on_startup": config.wifi.auto_connect_on_startup,
            },
            "sampling": config.sampling.model_dump(),
            "bluetooth": {
                "enabled": config.bluetooth.enabled,
                "device_name": config.bluetooth.device_name,
                "backend": config.bluetooth.backend,
                "service_uuid": config.bluetooth.service_uuid,
                "password_set": bool(config.bluetooth.password.get_secret_value()),
                "require_auth": config.bluetooth.require_auth,
            },
            "cardmed": {
                "enabled": config.cardmed.enabled,
                "site_id": config.cardmed.site_id,
                "device_label": config.cardmed.device_label,
                "location": config.cardmed.location,
                "operator_id": config.cardmed.operator_id,
                "test_upload_enabled": config.cardmed.test_upload_enabled,
                "extra_keys": list(config.cardmed.extra.keys()),
                "metadata_keys": list(config.cardmed.metadata.keys()),
            },
            "resilience": {
                "enabled": config.resilience.enabled,
                "wifi_reconnect_enabled": config.resilience.wifi_reconnect_enabled,
                "pause_sampling_without_wifi": config.resilience.pause_sampling_without_wifi,
                "pause_sampling_without_camera": config.resilience.pause_sampling_without_camera,
            },
            "camera": config.camera.model_dump(),
        }
