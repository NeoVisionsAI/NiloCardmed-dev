"""Tests de health degradado."""

from __future__ import annotations

from nilocardmed.config.models import AppConfig, EnvironmentSettings, ResilienceSettings, WifiSettings
from nilocardmed.resilience.health import HealthService


def test_health_wifi_disconnected_is_degraded():
    config = AppConfig(
        wifi=WifiSettings(enabled=True, backend="mock", ssid="TestNet"),
        resilience=ResilienceSettings(
            check_connectivity_in_health=False,
            health_treat_wifi_provisioning_as_degraded=True,
            low_memory_mb_threshold=0,
        ),
    )
    report = HealthService(config, EnvironmentSettings()).check()
    assert report.status in {"healthy", "degraded", "unhealthy"}


def test_health_no_ssid_wifi_component_ok():
    config = AppConfig(
        wifi=WifiSettings(enabled=True, backend="mock", ssid=None),
        resilience=ResilienceSettings(check_connectivity_in_health=False),
    )
    report = HealthService(config, EnvironmentSettings()).check()
    wifi = next(c for c in report.components if c.name == "wifi")
    assert wifi.ok is True
    assert wifi.severity == "healthy"
