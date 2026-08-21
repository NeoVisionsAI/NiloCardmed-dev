"""Tests de estado LE advertising."""

from __future__ import annotations

from unittest.mock import MagicMock, mock_open, patch

from nilocardmed.bluetooth.advertising_status import (
    diagnose_bluetooth_visibility,
    read_le_advertising_state,
)


def test_read_le_advertising_state_detects_active_instances():
    output = "Powered: yes\nDiscoverable: yes\nAdvertising: no\nActiveInstances: 0x01 (1)\n"
    with patch("nilocardmed.bluetooth.adapter_visibility.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
        state = read_le_advertising_state()

    assert state["discoverable"] is True
    assert state["advertising"] is False
    assert state["active_instances"] == 1
    assert state["le_advertising_active"] is True


def test_diagnose_reports_not_visible_without_le_advert():
    output = "Powered: yes\nDiscoverable: yes\nPairable: yes\nAdvertising: no\nActiveInstances: 0x00 (0)\n"
    with patch("nilocardmed.bluetooth.adapter_visibility.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
        report = diagnose_bluetooth_visibility()

    assert report["visible_for_ble_scan"] is False
    assert report["adapter"]["discoverable"] is True


def test_read_bluez_experimental_enabled_reads_main_conf():
    from nilocardmed.bluetooth.advertising_status import read_bluez_experimental_enabled

    with patch("builtins.open", mock_open(read_data="[General]\nExperimental=true\n")):
        assert read_bluez_experimental_enabled() is True
