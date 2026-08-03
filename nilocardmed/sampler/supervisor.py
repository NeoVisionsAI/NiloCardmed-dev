"""Supervisor del hilo de muestreo: reinicio si muere inesperadamente."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from nilocardmed.config.manager import ConfigManager
from nilocardmed.telemetry.store import telemetry

if TYPE_CHECKING:
    from nilocardmed.sampler.engine import SamplerEngine

logger = logging.getLogger(__name__)


class SamplerThreadSupervisor:
    """Monitoriza y reinicia el hilo del motor de muestreo si termina."""

    def __init__(
        self,
        config_manager: ConfigManager,
        *,
        sampler_engine: SamplerEngine,
        start_sampler: Callable[[], threading.Thread],
    ) -> None:
        self._config_manager = config_manager
        self._sampler_engine = sampler_engine
        self._start_sampler = start_sampler
        self._thread: threading.Thread | None = None
        self._sampler_thread: threading.Thread | None = None
        self._restart_count = 0

    @property
    def sampler_thread(self) -> threading.Thread | None:
        return self._sampler_thread

    def attach_thread(self, thread: threading.Thread) -> None:
        self._sampler_thread = thread

    def start(self, shutdown: threading.Event) -> None:
        if self._thread and self._thread.is_alive():
            return

        def _runner() -> None:
            try:
                self.run(shutdown)
            except Exception:
                logger.exception("Error en supervisor del hilo sampler")

        self._thread = threading.Thread(target=_runner, name="sampler-supervisor", daemon=True)
        self._thread.start()

    def join(self, timeout: float = 5) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def run(self, shutdown: threading.Event) -> None:
        logger.info("Supervisor del hilo sampler iniciado")
        while not shutdown.wait(timeout=10):
            config = self._config_manager.get()
            resilience = config.resilience
            if not config.sampling.enabled or not resilience.sampler_thread_supervisor_enabled:
                continue

            thread = self._sampler_thread
            if thread is None or thread.is_alive():
                continue

            logger.error("Hilo sampler terminado inesperadamente; reiniciando")
            telemetry.record_event(
                "sampler_thread_restart",
                "Reinicio del hilo sampler por muerte inesperada",
            )
            self._restart_sampler_thread()

    def _restart_sampler_thread(self) -> None:
        self._restart_count += 1
        new_thread = self._start_sampler()
        self._sampler_thread = new_thread
        logger.info("Hilo sampler reiniciado (total=%s)", self._restart_count)
