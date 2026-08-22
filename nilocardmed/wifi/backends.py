"""Backends parametrizables para gestión WiFi."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from nilocardmed.config.models import WifiSettings
from nilocardmed.wifi.exceptions import WifiBackendError, WifiConfigError, WifiConnectionError
from nilocardmed.wifi.models import WifiNetwork, WifiScanResult, WifiStatus

logger = logging.getLogger(__name__)

_SECURITY_UNKNOWN = "UNKNOWN"


class WifiBackend(ABC):
    """Interfaz común para escanear, conectar y consultar WiFi."""

    name: str

    def __init__(self, settings: WifiSettings) -> None:
        self.settings = settings

    @abstractmethod
    def scan(self, *, rescan: bool = False) -> WifiScanResult:
        raise NotImplementedError

    @abstractmethod
    def status(self) -> WifiStatus:
        raise NotImplementedError

    @abstractmethod
    def connect(self, ssid: str, password: str | None = None) -> WifiStatus:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> WifiStatus:
        raise NotImplementedError

    def verify_connectivity(self) -> bool:
        if not self.settings.verify_connectivity:
            return True
        try:
            response = httpx.get(
                self.settings.connectivity_check_url,
                timeout=self.settings.connectivity_timeout_seconds,
                follow_redirects=False,
            )
            return response.status_code in (204, 200)
        except httpx.HTTPError as exc:
            logger.warning("Comprobación de conectividad fallida: %s", exc)
            return False


class HostScriptBackend(WifiBackend):
    """Opción A: script montado desde el host (recomendado en Docker/Pi)."""

    name = "host_script"

    def _run(
        self,
        *args: str,
        password: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> dict:
        script = Path(self.settings.host_script_path)
        if not script.exists():
            raise WifiBackendError(f"Script WiFi no encontrado: {script}")
        if not os.access(script, os.X_OK):
            raise WifiBackendError(f"Script WiFi no ejecutable: {script}")

        env = os.environ.copy()
        env["WIFI_INTERFACE"] = self.settings.interface
        env["WIFI_SCAN_TIMEOUT"] = str(self.settings.scan_timeout_seconds)
        env["WIFI_CONNECT_TIMEOUT"] = str(self.settings.connect_timeout_seconds)
        if password is not None:
            env["WIFI_PASSWORD"] = password
        if extra_env:
            env.update(extra_env)

        try:
            result = subprocess.run(
                [str(script), *args],
                capture_output=True,
                text=True,
                timeout=max(self.settings.connect_timeout_seconds, self.settings.scan_timeout_seconds) + 5,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WifiBackendError(f"Timeout ejecutando script WiFi: {exc}") from exc

        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "Error desconocido"
            if args and args[0] == "connect":
                payload: dict = {}
                stdout = result.stdout.strip()
                if stdout:
                    try:
                        payload = json.loads(stdout)
                    except json.JSONDecodeError:
                        payload = {}
                raise WifiConnectionError(
                    str(payload.get("error", message)),
                    restored_previous=bool(payload.get("restored_previous")),
                    previous_ssid=payload.get("previous_ssid"),
                    attempted_ssid=payload.get("attempted_ssid"),
                )
            raise WifiBackendError(message)

        output = result.stdout.strip()
        if not output:
            return {}
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise WifiBackendError(f"Salida JSON inválida del script WiFi: {output[:200]}") from exc

    def scan(self, *, rescan: bool = False) -> WifiScanResult:
        payload = self._run(
            "scan",
            extra_env={"WIFI_FORCE_RESCAN": "1" if rescan else "0"},
        )
        networks = [_network_from_dict(item) for item in payload.get("networks", [])]
        return WifiScanResult(
            networks=networks,
            scan_mode=str(payload.get("scan_mode", "list")),
            connected_preserved=bool(payload.get("connected_preserved", False)),
            connection_restored=bool(payload.get("connection_restored", False)),
            previous_ssid=payload.get("previous_ssid"),
        )

    def capture_connection_snapshot(self) -> dict | None:
        payload = self._run("snapshot")
        if not payload or not payload.get("ssid"):
            return None
        return {
            "connection": payload.get("connection"),
            "ssid": payload.get("ssid"),
        }

    def restore_connection(self, snapshot: dict | None) -> bool:
        if not snapshot:
            return False
        payload = self._run(
            "restore",
            extra_env={"WIFI_SNAPSHOT": json.dumps(snapshot, ensure_ascii=False)},
        )
        return bool(payload.get("restored"))

    def status(self) -> WifiStatus:
        payload = self._run("status")
        return _status_from_dict(payload, self.settings.interface)

    def connect(self, ssid: str, password: str | None = None) -> WifiStatus:
        payload = self._run("connect", ssid, password=password or "")
        status = _status_from_dict(payload, self.settings.interface)
        if not status.connected:
            raise WifiConnectionError(f"No se pudo conectar a {ssid}")
        return status

    def disconnect(self) -> WifiStatus:
        payload = self._run("disconnect")
        return _status_from_dict(payload, self.settings.interface)


class NmcliBackend(WifiBackend):
    """Opción B: nmcli directo (requiere NetworkManager y D-Bus accesibles)."""

    name = "nmcli"

    def _run_nmcli(self, *args: str, timeout: int | None = None) -> str:
        binary = self.settings.nmcli_binary
        if shutil.which(binary) is None:
            raise WifiBackendError(f"{binary} no disponible")

        try:
            result = subprocess.run(
                [binary, *args],
                capture_output=True,
                text=True,
                timeout=timeout or self.settings.scan_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WifiBackendError(f"Timeout ejecutando nmcli: {exc}") from exc

        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            if "connect" in args:
                raise WifiConnectionError(message)
            raise WifiBackendError(message)
        return result.stdout

    def _connection_snapshot(self) -> dict | None:
        status = self.status()
        if not status.connected:
            return None
        output = self._run_nmcli(
            "-t",
            "-f",
            "GENERAL.CONNECTION",
            "dev",
            "show",
            self.settings.interface,
            timeout=5,
        )
        connection = None
        for line in output.splitlines():
            if line.startswith("GENERAL.CONNECTION:"):
                value = line.split(":", 1)[1].strip()
                connection = value if value != "--" else None
        return {
            "connection": connection,
            "ssid": status.ssid,
        }

    def _restore_connection(self, snapshot: dict | None) -> bool:
        if not snapshot:
            return False

        connection = snapshot.get("connection")
        if connection and connection not in {"", "--"}:
            result = subprocess.run(
                [
                    self.settings.nmcli_binary,
                    "connection",
                    "up",
                    connection,
                    "ifname",
                    self.settings.interface,
                ],
                capture_output=True,
                text=True,
                timeout=self.settings.connect_timeout_seconds,
                check=False,
            )
            if result.returncode == 0:
                return True

        ssid = snapshot.get("ssid")
        if ssid:
            result = subprocess.run(
                [
                    self.settings.nmcli_binary,
                    "dev",
                    "wifi",
                    "connect",
                    ssid,
                    "ifname",
                    self.settings.interface,
                ],
                capture_output=True,
                text=True,
                timeout=self.settings.connect_timeout_seconds,
                check=False,
            )
            return result.returncode == 0
        return False

    def capture_connection_snapshot(self) -> dict | None:
        return self._connection_snapshot()

    def restore_connection(self, snapshot: dict | None) -> bool:
        return self._restore_connection(snapshot)

    def scan(self, *, rescan: bool = False) -> WifiScanResult:
        snapshot = self._connection_snapshot()
        scan_mode = "rescan_with_restore" if snapshot else "rescan"

        self._run_nmcli(
            "dev",
            "wifi",
            "rescan",
            "ifname",
            self.settings.interface,
            timeout=10,
        )
        time.sleep(min(2.5, self.settings.scan_timeout_seconds / 2))

        output = self._run_nmcli(
            "-t",
            "-f",
            "SSID,SIGNAL,SECURITY,BSSID,FREQ,IN-USE",
            "dev",
            "wifi",
            "list",
            "ifname",
            self.settings.interface,
        )
        networks = _parse_nmcli_wifi_list(output)

        if len(networks) <= 1:
            output_all = self._run_nmcli(
                "-t",
                "-f",
                "SSID,SIGNAL,SECURITY,BSSID,FREQ,IN-USE",
                "dev",
                "wifi",
                "list",
                timeout=10,
            )
            networks_all = _parse_nmcli_wifi_list(output_all)
            if len(networks_all) > len(networks):
                networks = networks_all
                scan_mode = f"{scan_mode}+nmcli_all"

        restored = False
        if snapshot:
            after = self._connection_snapshot()
            if not after or after.get("ssid") != snapshot.get("ssid"):
                restored = self._restore_connection(snapshot)

        still = self._connection_snapshot()
        return WifiScanResult(
            networks=networks,
            scan_mode=scan_mode,
            connected_preserved=still is not None,
            connection_restored=restored,
            previous_ssid=snapshot.get("ssid") if snapshot else None,
        )

    def status(self) -> WifiStatus:
        output = self._run_nmcli(
            "-t",
            "-f",
            "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY",
            "dev",
            "show",
            self.settings.interface,
        )
        state = None
        ssid = None
        ip_address = None
        gateway = None
        for line in output.splitlines():
            if line.startswith("GENERAL.STATE:"):
                state = line.split(":", 1)[1].strip()
            elif line.startswith("GENERAL.CONNECTION:"):
                value = line.split(":", 1)[1].strip()
                ssid = value if value != "--" else None
            elif line.startswith("IP4.ADDRESS"):
                ip_address = line.split(":", 1)[1].strip().split("/")[0]
            elif line.startswith("IP4.GATEWAY:"):
                gateway = line.split(":", 1)[1].strip()

        connected = bool(state and "(connected)" in state.lower())
        return WifiStatus(
            interface=self.settings.interface,
            connected=connected,
            ssid=ssid,
            ip_address=ip_address,
            gateway=gateway,
            state=state,
        )

    def connect(self, ssid: str, password: str | None = None) -> WifiStatus:
        snapshot = self._connection_snapshot()
        args = [
            self.settings.nmcli_binary,
            "dev",
            "wifi",
            "connect",
            ssid,
            "ifname",
            self.settings.interface,
        ]
        if password:
            args.extend(["password", password])

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=self.settings.connect_timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or f"No se pudo conectar a {ssid}"
            restored = False
            if snapshot and snapshot.get("ssid") != ssid:
                restored = self._restore_connection(snapshot)
            raise WifiConnectionError(
                message,
                restored_previous=restored,
                previous_ssid=snapshot.get("ssid") if snapshot else None,
                attempted_ssid=ssid,
            )

        status = self.status()
        if not status.connected or status.ssid != ssid:
            restored = False
            if snapshot and snapshot.get("ssid") != ssid:
                restored = self._restore_connection(snapshot)
            raise WifiConnectionError(
                f"No se pudo conectar a {ssid}",
                restored_previous=restored,
                previous_ssid=snapshot.get("ssid") if snapshot else None,
                attempted_ssid=ssid,
            )
        return status

    def disconnect(self) -> WifiStatus:
        self._run_nmcli("dev", "disconnect", self.settings.interface)
        return self.status()


class MockBackend(WifiBackend):
    """Backend simulado para desarrollo sin hardware WiFi."""

    name = "mock"

    def __init__(self, settings: WifiSettings) -> None:
        super().__init__(settings)
        self._connected_ssid: str | None = settings.ssid
        self._ip_address = "192.168.50.10" if settings.ssid else None

    def scan(self, *, rescan: bool = False) -> WifiScanResult:
        networks = [
            WifiNetwork(ssid="NiloCardmed-Lab", signal=82, security="WPA2"),
            WifiNetwork(ssid="SER-Guest", signal=64, security="WPA2"),
            WifiNetwork(ssid="OpenNetwork", signal=40, security="OPEN"),
        ]
        return WifiScanResult(networks=networks, scan_mode="mock", connected_preserved=True)

    def status(self) -> WifiStatus:
        return WifiStatus(
            interface=self.settings.interface,
            connected=self._connected_ssid is not None,
            ssid=self._connected_ssid,
            ip_address=self._ip_address,
            gateway="192.168.50.1" if self._connected_ssid else None,
            signal=75 if self._connected_ssid else None,
            state="connected" if self._connected_ssid else "disconnected",
            connectivity_ok=True if self._connected_ssid else False,
        )

    def connect(self, ssid: str, password: str | None = None) -> WifiStatus:
        self._connected_ssid = ssid
        self._ip_address = "192.168.50.10"
        return self.status()

    def disconnect(self) -> WifiStatus:
        self._connected_ssid = None
        self._ip_address = None
        return self.status()

    def verify_connectivity(self) -> bool:
        return self._connected_ssid is not None


def select_backend(settings: WifiSettings) -> WifiBackend:
    """Selecciona backend según configuración."""
    backend = settings.backend
    if backend == "mock":
        return MockBackend(settings)
    if backend == "host_script":
        return HostScriptBackend(settings)
    if backend == "nmcli":
        return NmcliBackend(settings)

    if backend == "auto":
        script = Path(settings.host_script_path)
        if script.exists() and os.access(script, os.X_OK):
            logger.debug("Backend WiFi auto -> host_script")
            return HostScriptBackend(settings)
        if shutil.which(settings.nmcli_binary):
            logger.debug("Backend WiFi auto -> nmcli")
            return NmcliBackend(settings)
        logger.warning("Backend WiFi auto -> mock (sin script ni nmcli)")
        return MockBackend(settings)

    raise WifiConfigError(f"Backend WiFi no soportado: {backend}")


def _safe_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_nmcli_wifi_list(output: str) -> list[WifiNetwork]:
    networks: dict[str, WifiNetwork] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split(":")
        if not parts or not parts[0].strip():
            continue
        ssid = parts[0].strip()
        signal = _safe_int(parts[1]) if len(parts) > 1 else None
        security = parts[2].strip() if len(parts) > 2 and parts[2] else _SECURITY_UNKNOWN
        bssid = parts[3].strip() if len(parts) > 3 and parts[3] else None
        freq = _safe_int(parts[4]) if len(parts) > 4 else None
        in_use = parts[5].strip().lower() in {"yes", "sí", "si", "1", "true"} if len(parts) > 5 else False
        networks[ssid] = WifiNetwork(
            ssid=ssid,
            signal=signal,
            security=security,
            bssid=bssid,
            frequency_mhz=freq,
            in_use=in_use,
        )
    return sorted(networks.values(), key=lambda item: item.signal or 0, reverse=True)


def _network_from_dict(data: dict) -> WifiNetwork:
    return WifiNetwork(
        ssid=str(data.get("ssid", "")),
        signal=_safe_int(str(data["signal"])) if data.get("signal") is not None else None,
        security=data.get("security"),
        bssid=data.get("bssid"),
        frequency_mhz=_safe_int(str(data["frequency_mhz"])) if data.get("frequency_mhz") else None,
        in_use=bool(data.get("in_use")),
    )


def _status_from_dict(data: dict, interface: str) -> WifiStatus:
    return WifiStatus(
        interface=data.get("interface", interface),
        connected=bool(data.get("connected")),
        ssid=data.get("ssid"),
        ip_address=data.get("ip_address"),
        gateway=data.get("gateway"),
        signal=_safe_int(str(data["signal"])) if data.get("signal") is not None else None,
        state=data.get("state"),
        connectivity_ok=data.get("connectivity_ok"),
    )
