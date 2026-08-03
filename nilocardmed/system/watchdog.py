"""Watchdog: reinicio controlado si el muestreo queda colgado."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING

from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import AppConfig
from nilocardmed.sampler.window import evaluate_window
from nilocardmed.telemetry.store import telemetry
from nilocardmed.wifi.exceptions import WifiError
from nilocardmed.wifi.service import WifiService

if TYPE_CHECKING:
    from nilocardmed.sampler.engine import SamplerEngine

logger = logging.getLogger(__name__)


class Watchdog:
    """Monitoriza actividad del muestreo y reinicia el proceso si está stale."""

    def __init__(
        self,
        config_manager: ConfigManager,
        *,
        sampler_engine: SamplerEngine | None = None,
    ) -> None:
        self._config_manager = config_manager
        self._sampler_engine = sampler_engine
        self._thread: threading.Thread | None = None

    def start(self, shutdown: threading.Event) -> None:
        if self._thread and self._thread.is_alive():
            return

        def _runner() -> None:
            try:
                self.run(shutdown)
            except Exception:
                logger.exception("Error en watchdog")

        self._thread = threading.Thread(target=_runner, name="watchdog", daemon=True)
        self._thread.start()

    def join(self, timeout: float = 5) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def run(self, shutdown: threading.Event) -> None:
        logger.info("Watchdog iniciado")
        while not shutdown.wait(timeout=10):
            config = self._config_manager.get()
            resilience = config.resilience
            if not resilience.watchdog_enabled or not config.sampling.enabled:
                continue

            if not self._sampler_should_be_active(config):
                continue

            stale = self._seconds_since_last_sampler_tick()
            if stale is None:
                continue

            if stale < resilience.watchdog_max_stale_seconds:
                continue

            telemetry.record_event(
                "watchdog_restart",
                f"Sin actividad del sampler en {int(stale)}s; reinicio controlado",
                data={"stale_seconds": stale, "pause_reason": telemetry.sampler_pause_reason},
            )
            logger.error(
                "Watchdog: sin tick del sampler en %ds (umbral %ds); saliendo para reinicio Docker",
                int(stale),
                resilience.watchdog_max_stale_seconds,
            )
            os._exit(resilience.watchdog_restart_exit_code)

    def _sampler_should_be_active(self, config: AppConfig) -> bool:
        sampling = config.sampling
        if not sampling.enabled:
            return False

        window = evaluate_window(sampling)
        if not window.active:
            return False

        if self._sampler_engine is not None and not self._sampler_engine.state.running:
            stop_reason = self._sampler_engine.state.stop_reason
            if stop_reason in {"window_end", "shutdown", "max_consecutive_failures"}:
                return False

        resilience = config.resilience
        if resilience.enabled and resilience.pause_sampling_without_wifi and config.wifi.enabled:
            try:
                status = WifiService(config.wifi, config_manager=self._config_manager).status(
                    check_connectivity=False
                )
            except WifiError:
                return False
            if not status.connected:
                return False

        return True

    def _seconds_since_last_sampler_tick(self) -> float | None:
        last = telemetry.last_sampler_tick_at_epoch
        if last is not None:
            return time.time() - last

        if self._sampler_engine is None:
            return None

        return time.time() - telemetry.started_at_epoch
