"""Servicio Bluetooth de alto nivel."""

from __future__ import annotations

import logging
import threading
import time

from nilocardmed.bluetooth.adapter_visibility import ensure_adapter_visibility
from nilocardmed.bluetooth.backends import BluetoothBackend, select_backend
from nilocardmed.bluetooth.exceptions import BluetoothConfigError
from nilocardmed.bluetooth.protocol import CommandRouter, build_router
from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import BluetoothSettings, EnvironmentSettings

logger = logging.getLogger(__name__)


class BluetoothService:
    """Orquesta el servidor GATT BLE y el protocolo de comandos."""

    def __init__(
        self,
        settings: BluetoothSettings,
        config_manager: ConfigManager,
        env: EnvironmentSettings,
    ) -> None:
        self.settings = settings
        self._config_manager = config_manager
        self._env = env
        self._router: CommandRouter | None = None
        self._backend: BluetoothBackend | None = None
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._master_shutdown: threading.Event | None = None
        self._restart_lock = threading.Lock()

    @property
    def router(self) -> CommandRouter:
        if self._router is None:
            self._router = build_router(self.settings, self._config_manager, self._env)
        return self._router

    @property
    def backend(self) -> BluetoothBackend:
        if self._backend is None:
            if not self.settings.enabled:
                raise BluetoothConfigError("Bluetooth deshabilitado (bluetooth.enabled=false)")
            self._backend = select_backend(self.settings, self.router)
        return self._backend

    def _adapter_address(self) -> str | None:
        return self.settings.adapter_address

    def start(self, shutdown: threading.Event | None = None) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.settings.enabled:
            logger.info("Bluetooth deshabilitado; no se inicia GATT")
            return

        self.ensure_adapter_visibility()

        self._master_shutdown = shutdown or self._shutdown
        backend = self.backend

        def _runner() -> None:
            logger.info("Iniciando servicio Bluetooth (backend=%s)", backend.name)
            backend.start(self._master_shutdown)

        self._thread = threading.Thread(target=_runner, name="bluetooth", daemon=True)
        self._thread.start()

    def stop(self, shutdown: threading.Event | None = None) -> None:
        target = shutdown or self._shutdown
        target.set()
        self._stop_backend()

    def restart(self, shutdown: threading.Event | None = None) -> None:
        """Reinicia el backend BLE sin apagar el resto del daemon."""
        master = shutdown or self._master_shutdown or self._shutdown
        with self._restart_lock:
            logger.info("Reiniciando servicio Bluetooth")
            self._stop_backend()
            time.sleep(2.0)
            self.ensure_adapter_visibility()
            self._backend = None
            self.start(master)

    def has_active_client(self) -> bool:
        if self._backend is None:
            return False
        checker = getattr(self._backend, "has_active_client", None)
        if callable(checker):
            return bool(checker())
        return False

    def ensure_adapter_visibility(self) -> bool:
        """Mantiene discoverable/pairable en on (reactiva si BlueZ los apagó)."""
        result = ensure_adapter_visibility(adapter_address=self._adapter_address())
        if not result["ok"]:
            logger.warning(
                "Discoverable/pairable no confirmados tras reintento: %s",
                result.get("after"),
            )
        return bool(result["ok"])

    def refresh_adapter_visibility(self) -> None:
        """Alias retrocompatible."""
        self.ensure_adapter_visibility()

    def request_advertising_refresh(self) -> bool:
        if self._backend is None:
            return False
        refresh = getattr(self._backend, "request_advertising_refresh", None)
        if callable(refresh):
            return bool(refresh())
        return False

    def is_publish_alive(self) -> bool:
        if self._backend is None:
            return False
        checker = getattr(self._backend, "is_publish_alive", None)
        if callable(checker):
            return bool(checker())
        return False

    def is_healthy(self) -> bool:
        if not self.settings.enabled:
            return True
        if self._backend is None:
            return False
        return self._backend.is_healthy()

    def _stop_backend(self) -> None:
        if self._backend is not None:
            self._backend.stop()
        if self._thread is not None:
            self._thread.join(timeout=15)
        self._thread = None

    def process_mock(self, raw: bytes | str) -> bytes:
        """Procesa un mensaje usando backend mock (tests/CLI)."""
        backend = self.backend
        from nilocardmed.bluetooth.backends import MockBluetoothBackend

        if not isinstance(backend, MockBluetoothBackend):
            backend = MockBluetoothBackend(self.settings, self.router)
        return backend.process(raw)

    def process_mock_frames(self, raw: bytes | str) -> list[bytes]:
        """Procesa un write RX y devuelve la lista de frames TX (mock/tests)."""
        backend = self.backend
        from nilocardmed.bluetooth.backends import MockBluetoothBackend

        if not isinstance(backend, MockBluetoothBackend):
            backend = MockBluetoothBackend(self.settings, self.router)
        return backend.process_frames(raw)
