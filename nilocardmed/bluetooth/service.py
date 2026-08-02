"""Servicio Bluetooth de alto nivel."""

from __future__ import annotations

import logging
import threading

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

    def start(self, shutdown: threading.Event | None = None) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.settings.enabled:
            logger.info("Bluetooth deshabilitado; no se inicia GATT")
            return

        master_shutdown = shutdown or self._shutdown
        backend = self.backend

        def _runner() -> None:
            logger.info("Iniciando servicio Bluetooth (backend=%s)", backend.name)
            backend.start(master_shutdown)

        self._thread = threading.Thread(target=_runner, name="bluetooth", daemon=True)
        self._thread.start()

    def stop(self, shutdown: threading.Event | None = None) -> None:
        target = shutdown or self._shutdown
        target.set()
        if self._backend is not None:
            self._backend.stop()
        if self._thread is not None:
            self._thread.join(timeout=10)

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
