"""Comprobaciones de salud de subsistemas."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from nilocardmed import __version__
from nilocardmed.camera.discovery import list_cameras
from nilocardmed.camera.exceptions import CameraError
from nilocardmed.config.models import AppConfig, EnvironmentSettings
from nilocardmed.resilience.models import ComponentHealth, HealthReport, HealthStatus
from nilocardmed.wifi.exceptions import WifiError
from nilocardmed.wifi.service import WifiService

if TYPE_CHECKING:
    from nilocardmed.sampler.engine import SamplerEngine

logger = logging.getLogger(__name__)


class HealthService:
    """Evalúa WiFi, cámara, SER, muestreo y recursos del host."""

    def __init__(
        self,
        config: AppConfig,
        env: EnvironmentSettings,
        *,
        sampler_engine: SamplerEngine | None = None,
    ) -> None:
        self._config = config
        self._env = env
        self._sampler_engine = sampler_engine

    def check(self) -> HealthReport:
        components = [
            self._check_wifi(),
            self._check_camera(),
            self._check_ser(),
            self._check_bluetooth(),
            self._check_sampler(),
            self._check_storage(),
            self._check_memory(),
        ]

        statuses = [component.severity for component in components if not component.ok]
        if any(status == "unhealthy" for status in statuses):
            status: HealthStatus = "unhealthy"
        elif any(status == "degraded" for status in statuses):
            status = "degraded"
        else:
            status = "healthy"

        healthy = status == "healthy"
        degraded = status == "degraded"
        return HealthReport(
            healthy=healthy,
            degraded=degraded,
            status=status,
            components=components,
            checked_at_epoch=time.time(),
        )

    def summary_dict(self) -> dict:
        report = self.check()
        return {
            "version": __version__,
            **report.to_dict(),
        }

    def _check_wifi(self) -> ComponentHealth:
        wifi = self._config.wifi
        if not wifi.enabled:
            return ComponentHealth("wifi", True, "WiFi deshabilitado", severity="healthy")

        if not wifi.ssid:
            return ComponentHealth(
                "wifi",
                True,
                "Sin SSID configurado (modo provisioning)",
                severity="healthy",
            )

        try:
            service = WifiService(wifi)
            status = service.status(
                check_connectivity=self._config.resilience.check_connectivity_in_health
            )
        except WifiError as exc:
            if self._config.resilience.health_treat_wifi_provisioning_as_degraded:
                return ComponentHealth("wifi", False, str(exc), severity="degraded")
            return ComponentHealth("wifi", False, str(exc), severity="unhealthy")

        if not status.connected:
            severity: HealthStatus = (
                "degraded"
                if self._config.resilience.health_treat_wifi_provisioning_as_degraded
                else "unhealthy"
            )
            return ComponentHealth(
                "wifi",
                False,
                "Sin conexión WiFi",
                status.to_dict(),
                severity=severity,
            )

        if self._config.resilience.check_connectivity_in_health and not status.connectivity_ok:
            return ComponentHealth(
                "wifi",
                False,
                "WiFi conectado pero sin conectividad externa",
                status.to_dict(),
                severity="degraded",
            )

        return ComponentHealth(
            "wifi",
            True,
            f"Conectado a {status.ssid}",
            status.to_dict(),
            severity="healthy",
        )

    def _check_camera(self) -> ComponentHealth:
        camera = self._config.camera
        resilience = self._config.resilience
        missing_severity: HealthStatus = (
            "degraded"
            if resilience.health_treat_missing_camera_as_degraded
            else "unhealthy"
        )
        try:
            devices = list_cameras(
                device_glob=camera.device_glob,
                v4l2_ctl_binary=camera.v4l2_ctl_binary,
                discovery_timeout_seconds=camera.discovery_timeout_seconds,
                include_non_capture=False,
            )
        except CameraError as exc:
            return ComponentHealth("camera", False, str(exc), severity=missing_severity)

        capture_devices = [device for device in devices if device.supports_capture]
        if not capture_devices:
            return ComponentHealth(
                "camera",
                False,
                "No hay cámara USB conectada",
                severity=missing_severity,
            )

        return ComponentHealth(
            "camera",
            True,
            f"{len(capture_devices)} cámara(s) detectada(s)",
            {"devices": [str(device.path) for device in capture_devices]},
            severity="healthy",
        )

    def _check_ser(self) -> ComponentHealth:
        ser = self._config.ser
        if not ser.enabled:
            return ComponentHealth("ser", True, "SER deshabilitado", severity="healthy")

        if not self._config.resilience.ser_health_check_enabled:
            return ComponentHealth("ser", True, "SER habilitado (sin probe HTTP)", severity="healthy")

        try:
            with httpx.Client(timeout=ser.timeout_seconds, verify=ser.verify_ssl) as client:
                response = client.head(ser.url)
                if response.status_code >= 400:
                    response = client.get(ser.url)
        except httpx.HTTPError as exc:
            return ComponentHealth("ser", False, f"SER no alcanzable: {exc}", severity="degraded")

        ok = response.status_code in set(ser.success_status_codes) or response.status_code < 500
        severity: HealthStatus = "healthy" if ok else "degraded"
        return ComponentHealth(
            "ser",
            ok,
            f"SER respondió HTTP {response.status_code}",
            {"url": ser.url, "status_code": response.status_code},
            severity=severity,
        )

    def _check_bluetooth(self) -> ComponentHealth:
        bt = self._config.bluetooth
        if not bt.enabled:
            return ComponentHealth("bluetooth", True, "Bluetooth deshabilitado", severity="healthy")
        return ComponentHealth(
            "bluetooth",
            True,
            f"BLE habilitado ({bt.backend})",
            {"device_name": bt.device_name, "backend": bt.backend},
            severity="healthy",
        )

    def _check_sampler(self) -> ComponentHealth:
        sampling = self._config.sampling
        if not sampling.enabled:
            return ComponentHealth("sampler", True, "Muestreo deshabilitado", severity="healthy")

        if self._sampler_engine is None:
            return ComponentHealth(
                "sampler",
                True,
                "Muestreo habilitado (estado no disponible)",
                severity="healthy",
            )

        state = self._sampler_engine.state
        data = state.to_dict()
        if not state.running and state.stop_reason in {"window_end", "shutdown"}:
            return ComponentHealth(
                "sampler",
                True,
                f"Muestreo detenido ({state.stop_reason})",
                data,
                severity="healthy",
            )

        if not state.running and state.stop_reason:
            return ComponentHealth(
                "sampler",
                False,
                f"Muestreo detenido ({state.stop_reason})",
                data,
                severity="unhealthy",
            )

        if state.consecutive_failures > 0:
            return ComponentHealth(
                "sampler",
                False,
                f"{state.consecutive_failures} fallos consecutivos",
                data,
                severity="degraded",
            )

        return ComponentHealth("sampler", True, "Muestreo activo", data, severity="healthy")

    def _check_storage(self) -> ComponentHealth:
        path = self._env.data_dir
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            return ComponentHealth("storage", False, f"No se pudo leer disco: {exc}", severity="unhealthy")

        free_mb = usage.free // (1024 * 1024)
        min_mb = self._config.resilience.min_free_disk_mb
        ok = free_mb >= min_mb
        severity: HealthStatus = "healthy" if ok else "unhealthy"
        return ComponentHealth(
            "storage",
            ok,
            f"Libre {free_mb} MiB en {path}",
            {"free_mb": free_mb, "min_free_mb": min_mb},
            severity=severity,
        )

    def _check_memory(self) -> ComponentHealth:
        threshold = self._config.resilience.low_memory_mb_threshold
        if threshold <= 0:
            return ComponentHealth("memory", True, "Comprobación de memoria deshabilitada", severity="healthy")

        meminfo = Path("/proc/meminfo")
        if not meminfo.exists():
            return ComponentHealth("memory", True, "Meminfo no disponible", severity="healthy")

        available_kb = _read_memavailable_kb(meminfo)
        if available_kb is None:
            return ComponentHealth("memory", True, "MemAvailable no legible", severity="healthy")

        available_mb = available_kb // 1024
        ok = available_mb >= threshold
        severity: HealthStatus = "healthy" if ok else "degraded"
        return ComponentHealth(
            "memory",
            ok,
            f"Memoria disponible ~{available_mb} MiB",
            {"available_mb": available_mb, "threshold_mb": threshold},
            severity=severity,
        )


def _read_memavailable_kb(meminfo: Path) -> int | None:
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None
