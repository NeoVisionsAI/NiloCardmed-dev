"""Servicio Bluetooth BLE/GATT."""

from nilocardmed.bluetooth.exceptions import (
    BluetoothAuthError,
    BluetoothBackendError,
    BluetoothConfigError,
    BluetoothError,
    BluetoothProtocolError,
)
from nilocardmed.bluetooth.models import CommandRequest, CommandResponse
from nilocardmed.bluetooth.protocol import CommandRouter, build_router
from nilocardmed.bluetooth.service import BluetoothService

__all__ = [
    "BluetoothAuthError",
    "BluetoothBackendError",
    "BluetoothConfigError",
    "BluetoothError",
    "BluetoothProtocolError",
    "BluetoothService",
    "CommandRequest",
    "CommandResponse",
    "CommandRouter",
    "build_router",
]
