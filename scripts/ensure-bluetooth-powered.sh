#!/usr/bin/env bash
# Enciende el adaptador Bluetooth del host y alinea el alias con el nombre BLE de .env.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
ENV_FILE="${ENV_FILE:-${INSTALL_DIR}/.env}"

read_env_value() {
  local key="$1"
  local file="$2"
  [[ -f "${file}" ]] || return 1
  grep -E "^${key}=" "${file}" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || return 1
}

if ! command -v bluetoothctl >/dev/null 2>&1; then
  echo "[nilocardmed][AVISO] bluetoothctl no disponible" >&2
  exit 0
fi

if command -v rfkill >/dev/null 2>&1; then
  rfkill unblock bluetooth 2>/dev/null || true
fi

if ! bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
  bluetoothctl power on 2>/dev/null || true
fi

ble_name=""
ble_name="$(read_env_value "NILOCARDMED_BLUETOOTH__DEVICE_NAME" "${ENV_FILE}" || true)"
if [[ -n "${ble_name}" ]]; then
  # Evita que el escaneo muestre el hostname (p. ej. "cardmed") en lugar de NiloCardmed-<uuid>.
  bluetoothctl system-alias "${ble_name}" 2>/dev/null || true
fi

for _ in $(seq 1 10); do
  if bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
    if [[ -n "${ble_name}" ]]; then
      echo "[nilocardmed] Bluetooth host: Powered=yes, alias=${ble_name}"
    else
      echo "[nilocardmed] Bluetooth host: Powered=yes"
    fi
    exit 0
  fi
  sleep 1
done

echo "[nilocardmed][ERROR] Adaptador Bluetooth no encendido (bluetoothctl show → Powered: no)" >&2
echo "[nilocardmed][ERROR] Prueba: sudo rfkill unblock bluetooth && sudo bluetoothctl power on" >&2
exit 1
