"""Servicio de alto nivel para gestión WiFi."""

from __future__ import annotations

import logging

from pydantic import SecretStr

from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import WifiSettings
from nilocardmed.wifi.backends import WifiBackend, select_backend
from nilocardmed.wifi.exceptions import WifiConfigError, WifiConnectionError, WifiError
from nilocardmed.wifi.models import WifiNetwork, WifiScanResult, WifiStatus

logger = logging.getLogger(__name__)


class WifiService:
    """Orquesta escaneo, conexión y comprobación WiFi."""

    def __init__(
        self,
        settings: WifiSettings,
        *,
        config_manager: ConfigManager | None = None,
    ) -> None:
        self.settings = settings
        self._config_manager = config_manager
        self._backend: WifiBackend | None = None

    @property
    def backend(self) -> WifiBackend:
        if self._backend is None:
            if not self.settings.enabled:
                raise WifiConfigError("WiFi deshabilitado (wifi.enabled=false)")
            self._backend = select_backend(self.settings)
        return self._backend

    def scan(self, *, rescan: bool = False) -> WifiScanResult:
        logger.info(
            "Escaneando redes WiFi (backend=%s, rescan=%s)",
            self.backend.name,
            rescan,
        )
        return self.backend.scan(rescan=rescan)

    def status(self, *, check_connectivity: bool | None = None) -> WifiStatus:
        status = self.backend.status()
        should_check = (
            self.settings.verify_connectivity
            if check_connectivity is None
            else check_connectivity
        )
        if should_check and status.connected:
            connectivity_ok = self.backend.verify_connectivity()
            return WifiStatus(
                interface=status.interface,
                connected=status.connected,
                ssid=status.ssid,
                ip_address=status.ip_address,
                gateway=status.gateway,
                signal=status.signal,
                state=status.state,
                connectivity_ok=connectivity_ok,
            )
        return status

    def connect(
        self,
        ssid: str,
        password: str | None = None,
        *,
        persist: bool | None = None,
    ) -> WifiStatus:
        persist_cfg = self.settings.persist_to_config if persist is None else persist
        logger.info("Conectando a WiFi ssid=%s backend=%s", ssid, self.backend.name)

        snapshot = None
        capture = getattr(self.backend, "capture_connection_snapshot", None)
        if callable(capture):
            snapshot = capture()

        status = self.backend.connect(ssid, password)
        if self.settings.verify_connectivity:
            connectivity_ok = self.backend.verify_connectivity()
            status = WifiStatus(
                interface=status.interface,
                connected=status.connected,
                ssid=status.ssid,
                ip_address=status.ip_address,
                gateway=status.gateway,
                signal=status.signal,
                state=status.state,
                connectivity_ok=connectivity_ok,
            )
            if not connectivity_ok:
                restored = False
                restore = getattr(self.backend, "restore_connection", None)
                if callable(restore) and snapshot and snapshot.get("ssid") != ssid:
                    restored = bool(restore(snapshot))
                raise WifiConnectionError(
                    "Conectado a la red pero sin conectividad externa verificable",
                    restored_previous=restored,
                    previous_ssid=snapshot.get("ssid") if snapshot else None,
                    attempted_ssid=ssid,
                )

        if persist_cfg and self._config_manager is not None:
            self._persist_credentials(ssid, password)

        return status

    def connect_configured(self) -> WifiStatus:
        """Conecta usando SSID/password de la configuración."""
        if not self.settings.ssid:
            raise WifiConfigError("No hay SSID configurado")
        password = (
            self.settings.password.get_secret_value() if self.settings.password else None
        )
        return self.connect(self.settings.ssid, password, persist=False)

    def disconnect(self) -> WifiStatus:
        logger.info("Desconectando WiFi (backend=%s)", self.backend.name)
        return self.backend.disconnect()

    def test_connectivity(self) -> bool:
        return self.backend.verify_connectivity()

    def _persist_credentials(self, ssid: str, password: str | None) -> None:
        if self._config_manager is None:
            return
        config = self._config_manager.get()
        config.wifi.ssid = ssid
        if password is not None:
            config.wifi.password = SecretStr(password)
        self._config_manager.save(config)
        logger.info("Credenciales WiFi persistidas en config")
