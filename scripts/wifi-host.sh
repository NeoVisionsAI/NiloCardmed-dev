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
import time

if shutil.which("nmcli") is None:
    print("nmcli no disponible", file=sys.stderr)
    sys.exit(1)

INTERFACE = os.environ.get("WIFI_INTERFACE", "wlan0")
PASSWORD = os.environ.get("WIFI_PASSWORD", "")
COMMAND = sys.argv[1] if len(sys.argv) > 1 else ""
ARG = sys.argv[2] if len(sys.argv) > 2 else ""
SCAN_WAIT_SECONDS = float(os.environ.get("WIFI_SCAN_WAIT_SECONDS", "2.5"))


def run_nmcli(*args: str, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["nmcli", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "nmcli error"
        print(message, file=sys.stderr)
        sys.exit(result.returncode)
    return result


def _parse_wifi_list(output: str) -> list[dict]:
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
        in_use = parts[5].strip().lower() in {"yes", "sí", "si", "1", "true"} if len(parts) > 5 else False
        networks[ssid] = {
            "ssid": ssid,
            "signal": signal,
            "security": security,
            "bssid": bssid,
            "frequency_mhz": freq,
            "in_use": in_use,
        }
    return sorted(networks.values(), key=lambda item: item.get("signal") or 0, reverse=True)


def connection_snapshot() -> dict | None:
    st = status()
    if not st.get("connected"):
        return None
    return {
        "connection": st.get("connection"),
        "ssid": st.get("ssid"),
    }


def restore_connection(snapshot: dict | None) -> bool:
    if not snapshot:
        return False

    connection = snapshot.get("connection")
    if connection and connection not in {"", "--"}:
        result = subprocess.run(
            ["nmcli", "connection", "up", connection, "ifname", INTERFACE],
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("WIFI_CONNECT_TIMEOUT", "30")),
            check=False,
        )
        if result.returncode == 0:
            return True

    ssid = snapshot.get("ssid")
    if ssid:
        result = subprocess.run(
            ["nmcli", "dev", "wifi", "connect", ssid, "ifname", INTERFACE],
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("WIFI_CONNECT_TIMEOUT", "30")),
            check=False,
        )
        return result.returncode == 0

    return False


def scan() -> dict:
    snapshot = connection_snapshot()
    cached_only = os.environ.get("WIFI_SCAN_CACHED_ONLY", "").lower() in ("1", "true", "yes")

    if cached_only:
        scan_mode = "cached"
    elif snapshot:
        scan_mode = "rescan_with_restore"
    else:
        scan_mode = "rescan"

    if not cached_only:
        subprocess.run(
            ["nmcli", "dev", "wifi", "rescan", "ifname", INTERFACE],
            check=False,
            capture_output=True,
        )
        time.sleep(SCAN_WAIT_SECONDS)

    output = run_nmcli(
        "-t",
        "-f",
        "SSID,SIGNAL,SECURITY,BSSID,FREQ,IN-USE",
        "dev",
        "wifi",
        "list",
        "ifname",
        INTERFACE,
    ).stdout
    ordered = _parse_wifi_list(output)

    restored = False
    if snapshot:
        after = connection_snapshot()
        if not after or after.get("ssid") != snapshot.get("ssid"):
            restored = restore_connection(snapshot)

    still = connection_snapshot()
    return {
        "networks": ordered,
        "scan_mode": scan_mode,
        "connection_restored": restored,
        "connected_preserved": still is not None,
        "previous_ssid": snapshot.get("ssid") if snapshot else None,
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
    connection = None
    ip_address = None
    gateway = None
    for line in output.splitlines():
        if line.startswith("GENERAL.STATE:"):
            state = line.split(":", 1)[1].strip()
        elif line.startswith("GENERAL.CONNECTION:"):
            value = line.split(":", 1)[1].strip()
            connection = value if value != "--" else None
        elif line.startswith("IP4.ADDRESS"):
            ip_address = line.split(":", 1)[1].strip().split("/")[0]
        elif line.startswith("IP4.GATEWAY:"):
            gateway = line.split(":", 1)[1].strip()

    connected = "(connected)" in state.lower()
    ssid = None
    if connected:
        list_output = run_nmcli(
            "-t",
            "-f",
            "SSID,IN-USE",
            "dev",
            "wifi",
            "list",
            "ifname",
            INTERFACE,
            check=False,
        ).stdout
        for line in list_output.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1].strip().lower() in {"yes", "sí", "si", "1", "true"}:
                ssid = parts[0].strip()
                break
        if ssid is None and connection:
            ssid = connection

    return {
        "interface": INTERFACE,
        "connected": connected,
        "connection": connection,
        "ssid": ssid,
        "ip_address": ip_address,
        "gateway": gateway,
        "state": state,
    }


def connect(ssid: str) -> dict:
    snapshot = connection_snapshot()
    args = ["dev", "wifi", "connect", ssid, "ifname", INTERFACE]
    if PASSWORD:
        args.extend(["password", PASSWORD])

    result = subprocess.run(
        ["nmcli", *args],
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("WIFI_CONNECT_TIMEOUT", "30")),
        check=False,
    )

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"No se pudo conectar a {ssid}"
        restored = False
        if snapshot and snapshot.get("ssid") != ssid:
            restored = restore_connection(snapshot)
        payload = status()
        payload.update(
            {
                "success": False,
                "error": message,
                "restored_previous": restored,
                "previous_ssid": snapshot.get("ssid") if snapshot else None,
                "attempted_ssid": ssid,
            }
        )
        print(json.dumps(payload, ensure_ascii=False))
        sys.exit(1)

    final = status()
    if not final.get("connected") or final.get("ssid") != ssid:
        restored = False
        if snapshot and snapshot.get("ssid") != ssid:
            restored = restore_connection(snapshot)
        payload = status()
        payload.update(
            {
                "success": False,
                "error": f"No se pudo conectar a {ssid}",
                "restored_previous": restored,
                "previous_ssid": snapshot.get("ssid") if snapshot else None,
                "attempted_ssid": ssid,
            }
        )
        print(json.dumps(payload, ensure_ascii=False))
        sys.exit(1)

    final["success"] = True
    return final


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
elif COMMAND == "snapshot":
    snap = connection_snapshot()
    print(json.dumps(snap or {}, ensure_ascii=False))
elif COMMAND == "restore":
    raw = os.environ.get("WIFI_SNAPSHOT", "")
    snap = json.loads(raw) if raw else None
    if snap == {}:
        snap = None
    restored = restore_connection(snap)
    payload = status()
    payload["restored"] = restored
    print(json.dumps(payload, ensure_ascii=False))
else:
    print(f"Comando no reconocido: {COMMAND}", file=sys.stderr)
    sys.exit(1)
PY
