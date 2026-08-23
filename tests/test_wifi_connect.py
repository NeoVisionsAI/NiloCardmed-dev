"""Conexión WiFi: probe de conectividad y modo tolerante."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nilocardmed.config.models import WifiSettings
from nilocardmed.wifi.exceptions import WifiConnectionError
from nilocardmed.wifi.models import WifiStatus
from nilocardmed.wifi.service import WifiService


def _connected_status(ssid: str = "Office") -> WifiStatus:
    return WifiStatus(
        interface="wlan0",
        connected=True,
        ssid=ssid,
        ip_address="192.168.1.50",
        gateway="192.168.1.1",
        signal=70,
        state="connected",
    )


def test_connect_succeeds_when_linked_but_connectivity_probe_fails():
    settings = WifiSettings(
        backend="mock",
        fail_connect_without_connectivity=False,
    )
    service = WifiService(settings, config_manager=MagicMock())
    backend = MagicMock()
    backend.connect.return_value = _connected_status()
    backend.verify_connectivity.return_value = False
    service._backend = backend

    status = service.connect("Office", "secret", persist=False)

    assert status.connected is True
    assert status.connectivity_ok is False
    backend.connect.assert_called_once()


def test_connect_raises_when_strict_connectivity_required():
    settings = WifiSettings(
        backend="mock",
        fail_connect_without_connectivity=True,
    )
    service = WifiService(settings)
    backend = MagicMock()
    backend.connect.return_value = _connected_status()
    backend.verify_connectivity.return_value = False
    service._backend = backend

    with pytest.raises(WifiConnectionError, match="conectividad externa"):
        service.connect("Office", "secret", persist=False)


def test_connectivity_probe_tries_fallback_urls():
    from nilocardmed.wifi.backends import NmcliBackend

    settings = WifiSettings(
        connectivity_check_url="http://example.invalid/probe",
        connectivity_timeout_seconds=2,
    )
    backend = NmcliBackend(settings)

    with patch("nilocardmed.wifi.backends.httpx.get") as http_get:
        http_get.side_effect = [
            Exception("fail primary"),
            MagicMock(status_code=204),
        ]
        assert backend.verify_connectivity() is True
        assert http_get.call_count == 2
