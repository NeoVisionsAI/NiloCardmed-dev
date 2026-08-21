"""Tests de visibilidad discoverable BlueZ."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nilocardmed.bluetooth.adapter_visibility import (
    ensure_adapter_visibility,
    read_adapter_state,
)


def test_read_adapter_state_parses_bluetoothctl_show():
    output = """
Controller AA:BB:CC:DD:EE:FF (public)
\tName: hci0
\tAlias: NiloCardmed-test
\tPowered: yes
\tDiscoverable: no
\tPairable: yes
"""
    with patch("nilocardmed.bluetooth.adapter_visibility.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
        state = read_adapter_state()

    assert state["powered"] == "yes"
    assert state["discoverable"] == "no"
    assert state["pairable"] == "yes"
    assert state["alias"] == "NiloCardmed-test"


def test_ensure_adapter_visibility_reactivates_discoverable():
    show_outputs = iter(
        [
            MagicMock(
                returncode=0,
                stdout="Powered: yes\nDiscoverable: no\nPairable: no\n",
                stderr="",
            ),
            MagicMock(
                returncode=0,
                stdout="Powered: yes\nDiscoverable: yes\nPairable: yes\n",
                stderr="",
            ),
        ]
    )

    with patch("nilocardmed.bluetooth.adapter_visibility.subprocess.run") as run:
        run.side_effect = lambda *args, **kwargs: (
            next(show_outputs)
            if args and args[0] and args[0][0] == "bluetoothctl" and args[0][1] == "show"
            else MagicMock(returncode=0, stdout="", stderr="")
        )
        result = ensure_adapter_visibility()

    assert result["changed"] is True
    assert result["ok"] is True
    assert run.call_count >= 3
