"""Gestión de conectividad WiFi."""

from nilocardmed.wifi.backends import select_backend
from nilocardmed.wifi.exceptions import (
    WifiBackendError,
    WifiConfigError,
    WifiConnectionError,
    WifiError,
)
from nilocardmed.wifi.models import WifiNetwork, WifiStatus
from nilocardmed.wifi.service import WifiService

__all__ = [
    "WifiBackendError",
    "WifiConfigError",
    "WifiConnectionError",
    "WifiError",
    "WifiNetwork",
    "WifiService",
    "WifiStatus",
    "select_backend",
]
