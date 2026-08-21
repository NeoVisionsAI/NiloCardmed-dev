#!/usr/bin/env bash
# Enciende el adaptador Bluetooth del host (requerido para GATT/BLE peripheral).
set -euo pipefail

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

for _ in $(seq 1 10); do
  if bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
    echo "[nilocardmed] Bluetooth host: Powered=yes"
    exit 0
  fi
  sleep 1
done

echo "[nilocardmed][ERROR] Adaptador Bluetooth no encendido (bluetoothctl show → Powered: no)" >&2
echo "[nilocardmed][ERROR] Prueba: sudo rfkill unblock bluetooth && sudo bluetoothctl power on" >&2
exit 1
