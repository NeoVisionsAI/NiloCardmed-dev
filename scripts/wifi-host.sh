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
NMCLI_MUTATING = frozenset({"connect", "disconnect", "restore"})


def nmcli_command(*args: str) -> list[str]:
    """nmcli; usa sudo -n en operaciones que modifican red si no somos root."""
    nmcli = shutil.which("nmcli") or "nmcli"
    base = [nmcli, *args]
    if COMMAND not in NMCLI_MUTATING or os.geteuid() == 0:
        return base
    if os.environ.get("WIFI_NMCLI_SUDO", "1").lower() in ("0", "false", "no"):
        return base
    sudo = shutil.which("sudo")
    if sudo:
        return [sudo, "-n", nmcli, *args]
    return base


def run_nmcli(*args: str, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        nmcli_command(*args),
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
            nmcli_command("connection", "up", connection, "ifname", INTERFACE),
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
            nmcli_command("dev", "wifi", "connect", ssid, "ifname", INTERFACE),
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("WIFI_CONNECT_TIMEOUT", "30")),
            check=False,
        )
        return result.returncode == 0

    return False


def _dbm_to_signal_percent(dbm: int | float | None) -> int | None:
    if dbm is None:
        return None
    try:
        value = float(dbm)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, int(2 * (value + 100))))


def iw_binary() -> str | None:
    candidates: list[str] = []
    for item in (
        os.environ.get("WIFI_IW_BINARY"),
        shutil.which("iw"),
        "/usr/sbin/iw",
        "/sbin/iw",
    ):
        if item and item not in candidates:
            candidates.append(item)

    for candidate in candidates:
        if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
            continue
        try:
            probe = subprocess.run(
                [candidate, "help"],
                capture_output=True,
                timeout=3,
                check=False,
            )
        except (FileNotFoundError, OSError):
            continue
        if probe.returncode in (0, 1):
            return candidate
    return None


def ap_interface_up() -> bool:
    """True si uap0 existe (AP+STA concurrente — nmcli suele devolver solo la red activa)."""
    ap_if = os.environ.get("WIFI_AP_INTERFACE", "uap0")
    # sysfs: no depende de `ip` (slim Docker puede no tener iproute2)
    if os.path.isdir(f"/sys/class/net/{ap_if}"):
        operstate_path = f"/sys/class/net/{ap_if}/operstate"
        if os.path.isfile(operstate_path):
            try:
                with open(operstate_path, encoding="utf-8") as handle:
                    state = handle.read().strip().lower()
                if state in {"up", "unknown", "dormant"}:
                    return True
            except OSError:
                pass
        return True

    ip_bin = (
        os.environ.get("WIFI_IP_BINARY")
        or shutil.which("ip")
        or ("/usr/sbin/ip" if os.path.isfile("/usr/sbin/ip") else None)
        or ("/sbin/ip" if os.path.isfile("/sbin/ip") else None)
    )
    if not ip_bin:
        return False

    try:
        result = subprocess.run(
            [ip_bin, "link", "show", ap_if],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return False
    if result.returncode != 0:
        return False
    text = result.stdout
    return "state UP" in text or "LOWER_UP" in text


def merge_network_lists(*lists: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for networks in lists:
        for item in networks:
            ssid = item.get("ssid")
            if not ssid:
                continue
            if ssid not in merged:
                merged[ssid] = dict(item)
                continue
            existing = merged[ssid]
            if (item.get("signal") or 0) > (existing.get("signal") or 0):
                existing["signal"] = item["signal"]
            for key in ("security", "bssid", "frequency_mhz"):
                if item.get(key) and (not existing.get(key) or existing.get(key) == "UNKNOWN"):
                    existing[key] = item[key]
            if item.get("in_use"):
                existing["in_use"] = True
    return sorted(merged.values(), key=lambda item: item.get("signal") or 0, reverse=True)


def iw_scan_networks(*, wait_seconds: float | None = None) -> list[dict]:
    """Escaneo con iw (necesario en Pi AP+STA: nmcli a menudo solo lista la red conectada)."""
    iw = iw_binary()
    if not iw:
        return []

    wait = wait_seconds if wait_seconds is not None else max(SCAN_WAIT_SECONDS, 2.0)

    try:
        subprocess.run(
            [iw, "dev", INTERFACE, "scan", "trigger"],
            check=False,
            capture_output=True,
            timeout=8,
        )
        time.sleep(wait)
        result = subprocess.run(
            [iw, "dev", INTERFACE, "scan", "dump"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return []

    if result.returncode != 0:
        return []

    networks: dict[str, dict] = {}
    current_signal_dbm: int | None = None
    current_freq: int | None = None
    current_bssid: str | None = None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("BSS "):
            current_signal_dbm = None
            current_freq = None
            current_bssid = None
            parts = stripped.split("(", 1)
            if parts:
                mac = parts[0].replace("BSS ", "").strip()
                current_bssid = mac if mac else None
            if len(parts) > 1 and "MHz" in parts[1]:
                try:
                    current_freq = int(parts[1].split("MHz")[0].split()[-1])
                except ValueError:
                    current_freq = None
        elif stripped.startswith("signal:"):
            try:
                current_signal_dbm = int(float(stripped.split("signal:")[1].split()[0]))
            except (ValueError, IndexError):
                current_signal_dbm = None
        elif stripped.startswith("SSID:"):
            ssid = stripped.split("SSID:", 1)[1].strip()
            if not ssid:
                continue
            entry = networks.get(ssid)
            signal = _dbm_to_signal_percent(current_signal_dbm)
            if entry is None or (signal or 0) > (entry.get("signal") or 0):
                networks[ssid] = {
                    "ssid": ssid,
                    "signal": signal,
                    "security": "UNKNOWN",
                    "bssid": current_bssid,
                    "frequency_mhz": current_freq,
                    "in_use": False,
                }

    return sorted(networks.values(), key=lambda item: item.get("signal") or 0, reverse=True)


def scan() -> dict:
    snapshot = connection_snapshot()
    force_rescan = os.environ.get("WIFI_FORCE_RESCAN", "1").lower() not in ("0", "false", "no")
    cached_only = os.environ.get("WIFI_SCAN_CACHED_ONLY", "").lower() in ("1", "true", "yes")
    ap_concurrent = ap_interface_up()

    if cached_only:
        scan_mode = "cached"
    elif snapshot:
        scan_mode = "rescan_with_restore"
    else:
        scan_mode = "rescan"

    if ap_concurrent:
        scan_mode = f"{scan_mode}+ap_concurrent"

    scan_wait = max(SCAN_WAIT_SECONDS, 4.0) if ap_concurrent else SCAN_WAIT_SECONDS
    iw_networks: list[dict] = []

    # Con uap0 activo, nmcli suele devolver solo la BSS conectada — iw primero.
    if not cached_only and (ap_concurrent or force_rescan):
        iw_networks = iw_scan_networks(wait_seconds=scan_wait)

    if force_rescan and not cached_only:
        subprocess.run(
            ["nmcli", "dev", "wifi", "rescan", "ifname", INTERFACE],
            check=False,
            capture_output=True,
        )
        time.sleep(scan_wait)

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

    if len(ordered) <= 1:
        output_all = run_nmcli(
            "-t",
            "-f",
            "SSID,SIGNAL,SECURITY,BSSID,FREQ,IN-USE",
            "dev",
            "wifi",
            "list",
            check=False,
        ).stdout
        ordered_all = _parse_wifi_list(output_all)
        if len(ordered_all) > len(ordered):
            ordered = ordered_all
            scan_mode = f"{scan_mode}+nmcli_all"

    if not iw_networks and (ap_concurrent or len(ordered) <= 1):
        iw_networks = iw_scan_networks(wait_seconds=scan_wait)

    if iw_networks:
        ordered = merge_network_lists(ordered, iw_networks)
        scan_mode = f"{scan_mode}+iw"

    if ap_concurrent and len(ordered) <= 1:
        scan_mode = f"{scan_mode}+iw_missing"

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
        nmcli_command(*args),
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
    subprocess.run(nmcli_command("dev", "disconnect", INTERFACE), check=False)
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
