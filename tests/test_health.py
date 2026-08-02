"""Tests de health check."""

from __future__ import annotations

from nilocardmed.config.models import AppConfig, EnvironmentSettings, ResilienceSettings, WifiSettings
from nilocardmed.resilience.health import HealthService


def test_health_wifi_disabled_ok():
    config = AppConfig(
        wifi=WifiSettings(enabled=False),
        resilience=ResilienceSettings(check_connectivity_in_health=False),
    )
    report = HealthService(config, EnvironmentSettings()).check()
    wifi = next(c for c in report.components if c.name == "wifi")
    assert wifi.ok is True


def test_health_mock_wifi_connected():
    config = AppConfig(
        wifi=WifiSettings(enabled=True, backend="mock", ssid="TestNet"),
        resilience=ResilienceSettings(
            check_connectivity_in_health=False,
            low_memory_mb_threshold=0,
        ),
    )
    report = HealthService(config, EnvironmentSettings()).check()
    wifi = next(c for c in report.components if c.name == "wifi")
    assert wifi.ok is True
