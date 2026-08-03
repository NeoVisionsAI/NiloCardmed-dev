"""Supervisor de resiliencia: reconexión WiFi y monitorización periódica."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import EnvironmentSettings
from nilocardmed.resilience.health import HealthService
from nilocardmed.storage.manager import StorageManager
from nilocardmed.telemetry.store import telemetry
from nilocardmed.wifi.exceptions import WifiError
from nilocardmed.wifi.service import WifiService

if TYPE_CHECKING:
    from nilocardmed.sampler.engine import SamplerEngine

logger = logging.getLogger(__name__)


class ResilienceSupervisor:
    """Hilo background para recuperación automática y logs de salud."""

    def __init__(
        self,
        config_manager: ConfigManager,
        env: EnvironmentSettings,
        *,
        sampler_engine: SamplerEngine | None = None,
    ) -> None:
        self._config_manager = config_manager
        self._env = env
        self._sampler_engine = sampler_engine
        self._thread: threading.Thread | None = None
        self._last_health_log = 0.0
        self._last_wifi_attempt = 0.0
        self._last_pending_retry = 0.0
        self._last_disk_purge = 0.0

    def start(self, shutdown: threading.Event) -> None:
        if self._thread and self._thread.is_alive():
            return

        def _runner() -> None:
            try:
                self.run(shutdown)
            except Exception:
                logger.exception("Error fatal en supervisor de resiliencia")

        self._thread = threading.Thread(target=_runner, name="resilience", daemon=True)
        self._thread.start()

    def join(self, timeout: float = 10) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def run(self, shutdown: threading.Event) -> None:
        logger.info("Supervisor de resiliencia iniciado")
        while not shutdown.is_set():
            config = self._config_manager.get()
            resilience = config.resilience
            if not resilience.enabled:
                if shutdown.wait(timeout=5):
                    break
                continue

            self._maybe_reconnect_wifi(config, resilience.wifi_reconnect_interval_seconds)
            self._maybe_log_health(config, resilience.log_health_summary_interval_seconds)
            self._maybe_storage_maintenance(config)

            if shutdown.wait(timeout=max(resilience.supervisor_tick_seconds, 1.0)):
                break

        logger.info("Supervisor de resiliencia detenido")

    def _maybe_reconnect_wifi(self, config, interval_seconds: int) -> None:
        if not config.resilience.wifi_reconnect_enabled:
            return
        if not config.wifi.enabled or not config.wifi.ssid:
            return

        now = time.monotonic()
        if now - self._last_wifi_attempt < interval_seconds:
            return
        self._last_wifi_attempt = now

        service = WifiService(config.wifi, config_manager=self._config_manager)
        try:
            status = service.status(
                check_connectivity=config.resilience.wifi_reconnect_on_connectivity_loss,
            )
        except WifiError as exc:
            logger.warning("Reconexión WiFi: no se pudo leer estado: %s", exc)
            return

        needs_reconnect = not status.connected
        if (
            config.resilience.wifi_reconnect_on_connectivity_loss
            and status.connected
            and status.connectivity_ok is False
        ):
            needs_reconnect = True

        if not needs_reconnect:
            return

        logger.info("Reconexión WiFi programada ssid=%s", config.wifi.ssid)
        try:
            new_status = service.connect_configured()
            logger.info(
                "Reconexión WiFi OK ssid=%s ip=%s",
                new_status.ssid,
                new_status.ip_address,
            )
        except WifiError as exc:
            logger.warning("Reconexión WiFi fallida: %s", exc)

    def _maybe_log_health(self, config, interval_seconds: int) -> None:
        if interval_seconds <= 0:
            return
        now = time.monotonic()
        if now - self._last_health_log < interval_seconds:
            return
        self._last_health_log = now

        report = HealthService(
            config,
            self._env,
            sampler_engine=self._sampler_engine,
        ).check()
        level = logging.INFO if report.healthy else logging.WARNING
        logger.log(
            level,
            "Salud del sistema healthy=%s degraded=%s componentes=%s",
            report.healthy,
            report.degraded,
            {c.name: c.ok for c in report.components},
        )

    def _maybe_storage_maintenance(self, config) -> None:
        if not config.storage.enabled:
            return

        storage = StorageManager(
            config.storage,
            self._env,
            captures_dir=self._captures_dir(config),
        )
        now = time.monotonic()

        retry_interval = config.resilience.pending_retry_interval_seconds
        if now - self._last_pending_retry >= retry_interval:
            self._last_pending_retry = now
            from nilocardmed.sampler.window import evaluate_window

            window_active = evaluate_window(config.sampling).active
            result = storage.upload_pending_batch(config, window_active=window_active)
            if result.get("uploaded", 0) > 0:
                logger.info(
                    "Cola pending: %s subidas, %s restantes",
                    result["uploaded"],
                    result.get("remaining", 0),
                )
                telemetry.record_event(
                    "pending_retry",
                    f"{result['uploaded']} capturas subidas desde pending",
                    data=result,
                )

        purge_interval = config.resilience.disk_purge_check_interval_seconds
        if now - self._last_disk_purge >= purge_interval:
            self._last_disk_purge = now
            purge = storage.enforce_disk_policy()
            if purge.get("purged", 0) > 0:
                logger.warning(
                    "Purga por disco: %s archivos eliminados (%.1f%% libre)",
                    purge["purged"],
                    purge["free_percent"],
                )
                telemetry.record_event("disk_purge", "Purga por espacio bajo", data=purge)

    def _captures_dir(self, config):
        from pathlib import Path

        if config.camera.capture_dir:
            return Path(config.camera.capture_dir)
        return self._env.data_dir / "captures"
