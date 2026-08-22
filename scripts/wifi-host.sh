#!/usr/bin/env bash
# Gestión WiFi en el host (NetworkManager).
# Uso: wifi-host.sh scan|status|connect SSID|disconnect

set -euo pipefail

export WIFI_INTERFACE="${WIFI_INTERFACE:-wlan0}"
export WIFI_PASSWORD="${WIFI_PASSWORD:-}"

exec python3 - "$1" "${2:-}" <<'PY'
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

if shutil.which("nmcli") is None:
    print("nmcli no disponible", file=sys.stderr)
    sys.exit(1)

INTERFACE = os.environ.get("WIFI_INTERFACE", "wlan0")
PASSWORD = os.environ.get("WIFI_PASSWORD", "")
COMMAND = sys.argv[1] if len(sys.argv) > 1 else ""
ARG = sys.argv[2] if len(sys.argv) > 2 else ""


def run_nmcli(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["nmcli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "nmcli error"
        print(message, file=sys.stderr)
        sys.exit(result.returncode)
    return result


def scan() -> dict:
    force_rescan = os.environ.get("WIFI_SCAN_RESCAN", "").lower() in ("1", "true", "yes")
    rescan_when_connected = os.environ.get("WIFI_SCAN_RESCAN_WHEN_CONNECTED", "").lower() in (
        "1",
        "true",
        "yes",
    )

    connected = False
    state_output = subprocess.run(
        ["nmcli", "-t", "-f", "GENERAL.STATE", "dev", "show", INTERFACE],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if state_output.returncode == 0:
        connected = "(connected)" in state_output.stdout.lower()

    scan_mode = "list"
    if connected and not (force_rescan and rescan_when_connected):
        # Pi con radio única: rescan activo puede cortar SSH/internet.
        scan_mode = "cached_connected"
    elif force_rescan or not connected:
        subprocess.run(
            ["nmcli", "dev", "wifi", "rescan", "ifname", INTERFACE],
            check=False,
            capture_output=True,
        )
        scan_mode = "rescan" if not connected else "rescan_connected"

    output = run_nmcli(
        "-t",
        "-f",
        "SSID,SIGNAL,SECURITY,BSSID,FREQ",
        "dev",
        "wifi",
        "list",
        "ifname",
        INTERFACE,
    ).stdout
    networks: dict[str, dict] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split(":")
        if not parts or not parts[0].strip():
            continue
        ssid = parts[0].strip()
        signal = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        security = parts[2].strip() if len(parts) > 2 and parts[2] else "UNKNOWN"
        bssid = parts[3].strip() if len(parts) > 3 and parts[3] else None
        freq = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None
        networks[ssid] = {
            "ssid": ssid,
            "signal": signal,
            "security": security,
            "bssid": bssid,
            "frequency_mhz": freq,
        }
    ordered = sorted(networks.values(), key=lambda item: item.get("signal") or 0, reverse=True)
    return {
        "networks": ordered,
        "scan_mode": scan_mode,
        "connected_preserved": connected and scan_mode == "cached_connected",
    }


def status() -> dict:
    output = run_nmcli(
        "-t",
        "-f",
        "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY",
        "dev",
        "show",
        INTERFACE,
    ).stdout
    state = ""
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
    connected = "(connected)" in state.lower()
    return {
        "interface": INTERFACE,
        "connected": connected,
        "ssid": ssid,
        "ip_address": ip_address,
        "gateway": gateway,
        "state": state,
    }


def connect(ssid: str) -> dict:
    args = ["dev", "wifi", "connect", ssid, "ifname", INTERFACE]
    if PASSWORD:
        args.extend(["password", PASSWORD])
    run_nmcli(*args, timeout=int(os.environ.get("WIFI_CONNECT_TIMEOUT", "30")))
    result = status()
    if not result.get("connected"):
        print(f"No se pudo conectar a {ssid}", file=sys.stderr)
        sys.exit(1)
    return result


def disconnect() -> dict:
    subprocess.run(["nmcli", "dev", "disconnect", INTERFACE], check=False)
    return status()


if COMMAND == "scan":
    print(json.dumps(scan(), ensure_ascii=False))
elif COMMAND == "status":
    print(json.dumps(status(), ensure_ascii=False))
elif COMMAND == "connect":
    if not ARG:
        print("Uso: connect SSID", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(connect(ARG), ensure_ascii=False))
elif COMMAND == "disconnect":
    print(json.dumps(disconnect(), ensure_ascii=False))
else:
    print(f"Comando no reconocido: {COMMAND}", file=sys.stderr)
    sys.exit(1)
PY
