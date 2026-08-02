"""Motor de muestreo periódico: captura + envío a SER."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from nilocardmed.camera.discovery import list_cameras
from nilocardmed.camera.exceptions import CameraError
from nilocardmed.camera.service import CameraService
from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import AppConfig, EnvironmentSettings, SamplingSettings
from nilocardmed.sampler.models import SampleCycleResult, SamplerState
from nilocardmed.sampler.window import WindowPhase, evaluate_window
from nilocardmed.ser_client.client import SerClient
from nilocardmed.ser_client.exceptions import SerUploadError
from nilocardmed.ser_client.models import SamplePayload
from nilocardmed.wifi.exceptions import WifiError
from nilocardmed.wifi.service import WifiService

logger = logging.getLogger(__name__)


class SamplerEngine:
    """Ejecuta ciclos periódicos de captura y envío según configuración."""

    def __init__(
        self,
        config_manager: ConfigManager,
        env: EnvironmentSettings,
    ) -> None:
        self._config_manager = config_manager
        self._env = env
        self._state = SamplerState()
        self._config_mtime: float | None = None
        self._last_reload_check: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> SamplerState:
        with self._lock:
            return self._state

    def run(self, shutdown: threading.Event) -> None:
        """Bucle principal de muestreo hasta señal de apagado o fin de ventana."""
        config = self._config_manager.get()
        sampling = config.sampling

        with self._lock:
            self._state.running = True
            self._state.stop_reason = None

        logger.info(
            "Iniciando muestreo interval=%ss window=[%s,%s] enabled=%s",
            sampling.interval_seconds,
            sampling.monitor_start,
            sampling.monitor_end,
            sampling.enabled,
        )

        if sampling.initial_delay_seconds > 0:
            logger.info("Esperando delay inicial de %.2fs", sampling.initial_delay_seconds)
            if self._wait(shutdown, sampling.initial_delay_seconds, sampling.tick_sleep_seconds):
                self._stop("shutdown")
                return

        try:
            while not shutdown.is_set():
                config = self._reload_config_if_needed()
                sampling = config.sampling

                if not sampling.enabled:
                    logger.debug("Muestreo deshabilitado; esperando...")
                    if self._wait(
                        shutdown, sampling.tick_sleep_seconds, sampling.tick_sleep_seconds
                    ):
                        break
                    continue

                window = evaluate_window(sampling)
                if not window.active:
                    if window.phase == WindowPhase.AFTER_END:
                        if sampling.after_window_behavior == "stop":
                            logger.info("Ventana de monitorización finalizada")
                            self._stop("window_end")
                            return
                        logger.info("Ventana finalizada; modo idle")
                        if self._wait(
                            shutdown, sampling.tick_sleep_seconds, sampling.tick_sleep_seconds
                        ):
                            break
                        continue

                    wait_seconds = self._seconds_until_window(window, sampling)
                    logger.info(
                        "Muestreo inactivo (%s); próxima comprobación en %.0fs",
                        window.phase.value,
                        min(wait_seconds, sampling.tick_sleep_seconds),
                    )
                    if self._wait(
                        shutdown,
                        min(wait_seconds, sampling.tick_sleep_seconds),
                        sampling.tick_sleep_seconds,
                    ):
                        break
                    continue

                cycle = self.run_once(config)
                if self._record_cycle(cycle, sampling):
                    self._stop("max_consecutive_failures")
                    return

                sleep_seconds = sampling.interval_seconds
                if not cycle.success and sampling.failure_backoff_seconds > 0:
                    sleep_seconds += sampling.failure_backoff_seconds

                if self._wait(shutdown, sleep_seconds, sampling.tick_sleep_seconds):
                    break
        finally:
            if self.state.running:
                self._stop("shutdown")

    def run_once(self, config: AppConfig | None = None) -> SampleCycleResult:
        """Ejecuta un único ciclo de captura y envío."""
        config = config or self._config_manager.get()
        sampling = config.sampling

        precheck = self._precheck_cycle(config)
        if precheck is not None:
            return precheck

        camera_service = CameraService(config.camera, data_dir=self._env.data_dir)

        try:
            capture = camera_service.capture()
        except CameraError as exc:
            logger.error("Error de captura en ciclo de muestreo: %s", exc)
            return SampleCycleResult(success=False, capture_error=str(exc))

        upload_result = None
        upload_error = None
        skipped_upload = False
        should_upload = sampling.upload_enabled and config.ser.enabled

        if should_upload:
            try:
                payload = SamplePayload(
                    image_bytes=capture.output_path.read_bytes(),
                    filename=capture.output_path.name,
                    captured_at=capture.captured_at,
                    device_id=config.ser.device_id,
                )
                upload_result = SerClient(config.ser).upload_sample(payload)
            except SerUploadError as exc:
                upload_error = str(exc)
                logger.error("Error de envío en ciclo de muestreo: %s", exc)
        else:
            skipped_upload = True
            logger.info(
                "Envío omitido (upload_enabled=%s, ser.enabled=%s)",
                sampling.upload_enabled,
                config.ser.enabled,
            )

        if should_upload:
            success = upload_error is None and upload_result is not None
        else:
            success = True

        if success and upload_result and sampling.delete_capture_after_upload:
            self._delete_capture(capture.output_path, keep=False)
        elif not success and not sampling.keep_capture_on_upload_failure:
            self._delete_capture(capture.output_path, keep=False)

        return SampleCycleResult(
            success=success,
            capture_path=str(capture.output_path),
            capture_backend=capture.backend,
            captured_at=capture.captured_at,
            upload=upload_result,
            upload_error=upload_error,
            skipped_upload=skipped_upload,
        )

    def _precheck_cycle(self, config: AppConfig) -> SampleCycleResult | None:
        """Comprueba WiFi/cámara antes de capturar (Fase 9)."""
        resilience = config.resilience
        if not resilience.enabled:
            return None

        if resilience.pause_sampling_without_wifi and config.wifi.enabled:
            try:
                wifi = WifiService(config.wifi, config_manager=self._config_manager)
                status = wifi.status(check_connectivity=False)
            except WifiError as exc:
                logger.warning("Precheck WiFi fallido: %s", exc)
                return SampleCycleResult(success=False, upload_error=f"wifi: {exc}")
            if not status.connected:
                logger.warning("Ciclo de muestreo omitido: WiFi desconectado")
                return SampleCycleResult(
                    success=False,
                    skipped_upload=True,
                    upload_error="wifi_not_connected",
                )

        if resilience.pause_sampling_without_camera:
            camera = config.camera
            try:
                devices = list_cameras(
                    device_glob=camera.device_glob,
                    v4l2_ctl_binary=camera.v4l2_ctl_binary,
                    discovery_timeout_seconds=camera.discovery_timeout_seconds,
                    include_non_capture=False,
                )
            except CameraError as exc:
                logger.warning("Precheck cámara fallido: %s", exc)
                return SampleCycleResult(success=False, capture_error=str(exc))
            if not any(device.supports_capture for device in devices):
                logger.warning("Ciclo de muestreo omitido: sin cámara USB")
                return SampleCycleResult(success=False, capture_error="no_camera_detected")

        return None

    def _record_cycle(self, cycle: SampleCycleResult, sampling: SamplingSettings) -> bool:
        """Registra el ciclo. Devuelve True si el muestreo debe detenerse."""
        with self._lock:
            self._state.cycles_total += 1
            self._state.last_cycle = cycle
            if cycle.success:
                self._state.cycles_success += 1
                self._state.consecutive_failures = 0
            else:
                self._state.cycles_failed += 1
                self._state.consecutive_failures += 1

            if (
                sampling.max_consecutive_failures > 0
                and self._state.consecutive_failures >= sampling.max_consecutive_failures
            ):
                logger.error(
                    "Máximo de fallos consecutivos alcanzado (%s)",
                    sampling.max_consecutive_failures,
                )
                return True
        return False

    def _reload_config_if_needed(self) -> AppConfig:
        sampling = self._config_manager.get().sampling
        path = self._config_manager.config_path

        if path.exists():
            mtime = path.stat().st_mtime
            if self._config_mtime is None:
                self._config_mtime = mtime
            elif mtime != self._config_mtime:
                logger.info("Configuración modificada en disco; recargando")
                self._config_mtime = mtime
                return self._config_manager.load()

        if sampling.config_reload_seconds > 0:
            elapsed = time.monotonic() - self._last_reload_check
            if elapsed >= sampling.config_reload_seconds:
                self._last_reload_check = time.monotonic()
                if path.exists():
                    self._config_mtime = path.stat().st_mtime
                return self._config_manager.load()

        return self._config_manager.get()

    @staticmethod
    def _wait(shutdown: threading.Event, seconds: float, tick: float) -> bool:
        """Espera interruptible. Devuelve True si se solicitó apagado."""
        deadline = time.monotonic() + max(seconds, 0)
        while not shutdown.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            shutdown.wait(timeout=min(remaining, max(tick, 0.1)))
            if shutdown.is_set():
                return True
        return True

    @staticmethod
    def _seconds_until_window(window, sampling: SamplingSettings) -> float:
        if window.phase == WindowPhase.BEFORE_START and window.seconds_until_start is not None:
            return max(window.seconds_until_start, sampling.tick_sleep_seconds)
        return sampling.tick_sleep_seconds

    @staticmethod
    def _delete_capture(path: Path, *, keep: bool) -> None:
        if keep:
            return
        try:
            path.unlink(missing_ok=True)
            logger.debug("Captura local eliminada: %s", path)
        except OSError as exc:
            logger.warning("No se pudo eliminar captura %s: %s", path, exc)

    def _stop(self, reason: str) -> None:
        with self._lock:
            self._state.running = False
            self._state.stop_reason = reason
        logger.info("Muestreo detenido (%s)", reason)
