"""Supervisor BLE: reinicia GATT si deja de publicarse (24/7)."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from nilocardmed.config.manager import ConfigManager
from nilocardmed.operations_log import trace_system
from nilocardmed.telemetry.store import telemetry

if TYPE_CHECKING:
    from nilocardmed.bluetooth.service import BluetoothService

logger = logging.getLogger(__name__)


class BluetoothSupervisor:
    """Monitoriza el backend BLE y lo reinicia si el hilo GATT muere o deja de estar activo."""

    def __init__(
        self,
        config_manager: ConfigManager,
        bluetooth_service: BluetoothService,
    ) -> None:
        self._config_manager = config_manager
        self._bluetooth_service = bluetooth_service
        self._thread: threading.Thread | None = None
        self._last_restart = 0.0
        self._restart_count = 0

    @property
    def restart_count(self) -> int:
        return self._restart_count

    def start(self, shutdown: threading.Event) -> None:
        if self._thread and self._thread.is_alive():
            return

        def _runner() -> None:
            try:
                self.run(shutdown)
            except Exception:
                logger.exception("Error en supervisor Bluetooth")

        self._thread = threading.Thread(target=_runner, name="bluetooth-supervisor", daemon=True)
        self._thread.start()

    def join(self, timeout: float = 5) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def run(self, shutdown: threading.Event) -> None:
        logger.info("Supervisor Bluetooth iniciado")
        while not shutdown.is_set():
            config = self._config_manager.get()
            resilience = config.resilience
            interval = max(resilience.bluetooth_health_check_interval_seconds, 5)

            if not config.bluetooth.enabled or not resilience.bluetooth_supervisor_enabled:
                if shutdown.wait(timeout=float(interval)):
                    break
                continue

            if not self._bluetooth_service.is_healthy():
                if self._bluetooth_service.has_active_client():
                    logger.debug("BLE con cliente activo; omitiendo reinicio automático")
                else:
                    self._maybe_restart(shutdown, resilience.bluetooth_restart_cooldown_seconds)

            if shutdown.wait(timeout=float(interval)):
                break

        logger.info("Supervisor Bluetooth detenido")

    def _maybe_restart(self, shutdown: threading.Event, cooldown_seconds: int) -> None:
        now = time.monotonic()
        if now - self._last_restart < cooldown_seconds:
            logger.debug(
                "BLE no saludable; reinicio en cooldown (%ss restantes)",
                int(cooldown_seconds - (now - self._last_restart)),
            )
            return

        self._last_restart = now
        self._restart_count += 1
        logger.warning(
            "BLE no saludable; reiniciando publicación GATT (intento %s)",
            self._restart_count,
        )
        trace_system(
            event="bluetooth_reinicio",
            detail="Supervisor reiniciando GATT",
            attempt=self._restart_count,
        )
        telemetry.record_event(
            "bluetooth_restart",
            "Reinicio automático del servicio BLE",
            data={"attempt": self._restart_count},
        )

        try:
            self._bluetooth_service.restart(shutdown)
        except Exception:
            logger.exception("Reinicio BLE fallido")
