"""Escaneo WiFi sin cortar enlace activo (Pi radio única)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nilocardmed.config.models import WifiSettings
from nilocardmed.wifi.backends import NmcliBackend


def test_nmcli_scan_skips_rescan_when_connected():
    settings = WifiSettings(scan_rescan_when_connected=False)
    backend = NmcliBackend(settings)

    with patch.object(backend, "_run_nmcli") as run_nmcli:
        run_nmcli.side_effect = [
            "GENERAL.STATE:100 (connected)\n",  # _interface_connected
            "Office:70:WPA2:aa:2437\n",  # list
        ]
        result = backend.scan(rescan=False)

    assert result.scan_mode == "cached_connected"
    assert result.connected_preserved is True
    assert len(result.networks) == 1
    assert result.networks[0].ssid == "Office"
    assert run_nmcli.call_count == 2
    assert "rescan" not in " ".join(str(call) for call in run_nmcli.call_args_list)


def test_nmcli_scan_rescans_when_disconnected():
    settings = WifiSettings(scan_rescan_when_connected=False)
    backend = NmcliBackend(settings)

    with patch.object(backend, "_run_nmcli") as run_nmcli:
        run_nmcli.side_effect = [
            "GENERAL.STATE:30 (disconnected)\n",
            "",
            "Guest:50:WPA2:bb:2437\n",
        ]
        result = backend.scan(rescan=False)

    assert result.scan_mode == "rescan"
    assert result.connected_preserved is False
    rescan_calls = [call for call in run_nmcli.call_args_list if "rescan" in str(call)]
    assert len(rescan_calls) == 1
