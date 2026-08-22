"""Modelos del módulo WiFi."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WifiNetwork:
    """Red WiFi detectada en un escaneo."""

    ssid: str
    signal: int | None = None
    security: str | None = None
    bssid: str | None = None
    frequency_mhz: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ssid": self.ssid,
            "signal": self.signal,
            "security": self.security,
            "bssid": self.bssid,
            "frequency_mhz": self.frequency_mhz,
        }


@dataclass(frozen=True, slots=True)
class WifiScanResult:
    """Resultado de escaneo WiFi (lista + metadatos de cómo se obtuvo)."""

    networks: list[WifiNetwork]
    scan_mode: str = "list"
    connected_preserved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "networks": [network.to_dict() for network in self.networks],
            "scan_mode": self.scan_mode,
            "connected_preserved": self.connected_preserved,
        }


@dataclass(frozen=True, slots=True)
class WifiStatus:
    """Estado actual de la interfaz WiFi."""

    interface: str
    connected: bool
    ssid: str | None = None
    ip_address: str | None = None
    gateway: str | None = None
    signal: int | None = None
    state: str | None = None
    connectivity_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": self.interface,
            "connected": self.connected,
            "ssid": self.ssid,
            "ip_address": self.ip_address,
            "gateway": self.gateway,
            "signal": self.signal,
            "state": self.state,
            "connectivity_ok": self.connectivity_ok,
        }
