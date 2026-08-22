"""Escaneo WiFi con rescan y restauración de enlace activo."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nilocardmed.bluetooth.command_errors import BluetoothCommandError
from nilocardmed.bluetooth.handlers import handle_wifi_connect
from nilocardmed.bluetooth.models import CommandRequest
from nilocardmed.bluetooth.protocol import CommandContext
from nilocardmed.config.models import WifiSettings
from nilocardmed.wifi.backends import HostScriptBackend, NmcliBackend
from nilocardmed.wifi.exceptions import WifiConnectionError


def test_nmcli_scan_rescans_and_preserves_connection_when_connected():
    settings = WifiSettings()
    backend = NmcliBackend(settings)

    with patch.object(backend, "_run_nmcli") as run_nmcli:
        run_nmcli.side_effect = [
            "GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:Office\nIP4.ADDRESS:10.0.0.2/24\n",
            "GENERAL.CONNECTION:Office\n",
            "",
            "Office:70:WPA2:aa:2437:yes\nGuest:50:WPA2:bb:2437:no\n",
            "GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:Office\nIP4.ADDRESS:10.0.0.2/24\n",
            "GENERAL.CONNECTION:Office\n",
            "GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:Office\nIP4.ADDRESS:10.0.0.2/24\n",
            "GENERAL.CONNECTION:Office\n",
        ]
        result = backend.scan(rescan=False)

    assert result.scan_mode == "rescan_with_restore"
    assert result.connected_preserved is True
    assert len(result.networks) == 2
    rescan_calls = [call for call in run_nmcli.call_args_list if "rescan" in str(call)]
    assert len(rescan_calls) == 1


def test_nmcli_scan_rescans_when_disconnected():
    settings = WifiSettings()
    backend = NmcliBackend(settings)

    with patch.object(backend, "_run_nmcli") as run_nmcli:
        run_nmcli.side_effect = [
            "GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--\n",
            "",
            "Guest:50:WPA2:bb:2437:no\n",
            "GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--\n",
        ]
        result = backend.scan(rescan=False)

    assert result.scan_mode == "rescan"
    assert result.connected_preserved is False
    assert result.networks[0].ssid == "Guest"


def test_host_script_connect_failure_parses_restore_metadata():
    settings = WifiSettings(host_script_path="/host/scripts/wifi-host.sh")
    backend = HostScriptBackend(settings)
    failure_payload = {
        "connected": True,
        "ssid": "Office",
        "success": False,
        "error": "Secrets were required",
        "restored_previous": True,
        "previous_ssid": "Office",
        "attempted_ssid": "Guest",
    }

    with patch.object(backend, "_run") as run_script:
        run_script.side_effect = WifiConnectionError(
            failure_payload["error"],
            restored_previous=True,
            previous_ssid="Office",
            attempted_ssid="Guest",
        )
        try:
            backend.connect("Guest", "bad-password")
        except WifiConnectionError as exc:
            assert exc.restored_previous is True
            assert exc.previous_ssid == "Office"
            assert exc.attempted_ssid == "Guest"
        else:
            raise AssertionError("Se esperaba WifiConnectionError")


def test_handle_wifi_connect_returns_restore_metadata_on_failure():
    request = CommandRequest(cmd="wifi_connect", token="tok", id="1", payload={"ssid": "Guest"})
    ctx = MagicMock(spec=CommandContext)
    ctx.config_manager.get.return_value.wifi = WifiSettings(backend="mock")
    service = MagicMock()
    service.connect.side_effect = WifiConnectionError(
        "Contraseña incorrecta",
        restored_previous=True,
        previous_ssid="Office",
        attempted_ssid="Guest",
    )

    with patch("nilocardmed.bluetooth.handlers.WifiService", return_value=service):
        try:
            handle_wifi_connect(ctx, request)
        except BluetoothCommandError as exc:
            assert exc.code == "wifi_connection_failed"
            assert exc.data == {
                "restored_previous": True,
                "previous_ssid": "Office",
                "attempted_ssid": "Guest",
            }
        else:
            raise AssertionError("Se esperaba BluetoothCommandError")
