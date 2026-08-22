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

deploy_bluetooth_enabled() {
  local deploy_file="${INSTALL_DIR}/deploy.env"
  if [[ -f "${deploy_file}" ]]; then
    # shellcheck disable=SC1091
    set -a && source "${deploy_file}" && set +a
  fi
  case "${BLUETOOTH_ENABLED:-false}" in 1 | true | yes | on) return 0 ;; esac
  case "${ENABLE_BLUETOOTH:-false}" in 1 | true | yes | on) return 0 ;; esac
  return 1
}

if ! deploy_bluetooth_enabled; then
  echo "[nilocardmed] Bluetooth deshabilitado — omitiendo ensure-bluetooth-powered"
  exit 0
fi

ensure_bluez_experimental() {
  local conf="/etc/bluetooth/main.conf"
  [[ -f "${conf}" ]] || return 0

  local changed=false
  if grep -qE '^Experimental\s*=\s*true' "${conf}" 2>/dev/null; then
    :
  elif grep -qE '^Experimental\s*=' "${conf}" 2>/dev/null; then
    sed -i 's/^Experimental\s*=.*/Experimental=true/' "${conf}"
    changed=true
  elif grep -qE '^\[General\]' "${conf}" 2>/dev/null; then
    sed -i '/^\[General\]/a Experimental=true' "${conf}"
    changed=true
  else
    printf '\n[General]\nExperimental=true\n' >> "${conf}"
    changed=true
  fi

  if ! grep -qE '^AutoEnable\s*=\s*true' "${conf}" 2>/dev/null; then
    if grep -qE '^AutoEnable\s*=' "${conf}" 2>/dev/null; then
      sed -i 's/^AutoEnable\s*=.*/AutoEnable=true/' "${conf}"
    elif grep -qE '^\[General\]' "${conf}" 2>/dev/null; then
      sed -i '/^\[General\]/a AutoEnable=true' "${conf}"
    fi
    changed=true
  fi

  # DiscoverableTimeout=0 → no apagar discoverable solo tras unos minutos (BlueZ default ~180s)
  if ! grep -qE '^DiscoverableTimeout\s*=\s*0' "${conf}" 2>/dev/null; then
    if grep -qE '^DiscoverableTimeout\s*=' "${conf}" 2>/dev/null; then
      sed -i 's/^DiscoverableTimeout\s*=.*/DiscoverableTimeout=0/' "${conf}"
    elif grep -qE '^\[General\]' "${conf}" 2>/dev/null; then
      sed -i '/^\[General\]/a DiscoverableTimeout=0' "${conf}"
    fi
    changed=true
  fi

  if ! grep -qE '^PairableTimeout\s*=\s*0' "${conf}" 2>/dev/null; then
    if grep -qE '^PairableTimeout\s*=' "${conf}" 2>/dev/null; then
      sed -i 's/^PairableTimeout\s*=.*/PairableTimeout=0/' "${conf}"
    elif grep -qE '^\[General\]' "${conf}" 2>/dev/null; then
      sed -i '/^\[General\]/a PairableTimeout=0' "${conf}"
    fi
    changed=true
  fi

  if ! grep -qE '^KernelExperimental\s*=\s*true' "${conf}" 2>/dev/null; then
    if grep -qE '^KernelExperimental\s*=' "${conf}" 2>/dev/null; then
      sed -i 's/^KernelExperimental\s*=.*/KernelExperimental=true/' "${conf}"
    elif grep -qE '^\[General\]' "${conf}" 2>/dev/null; then
      sed -i '/^\[General\]/a KernelExperimental=true' "${conf}"
    fi
    changed=true
  fi

  if [[ "${changed}" == true ]]; then
    echo "[nilocardmed] BlueZ: Experimental=true (LE advertisement requerido por Web Bluetooth)"
    systemctl restart bluetooth 2>/dev/null || service bluetooth restart 2>/dev/null || true
    sleep 2
  fi
}

if ! command -v bluetoothctl >/dev/null 2>&1; then
  echo "[nilocardmed][AVISO] bluetoothctl no disponible" >&2
  exit 0
fi

ensure_bluez_experimental

if command -v rfkill >/dev/null 2>&1; then
  rfkill unblock bluetooth 2>/dev/null || true
fi

if ! bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
  bluetoothctl power on 2>/dev/null || true
fi

bluetoothctl discoverable on 2>/dev/null || true
bluetoothctl pairable on 2>/dev/null || true

ble_name=""
ble_name="$(read_env_value "NILOCARDMED_BLUETOOTH__DEVICE_NAME" "${ENV_FILE}" || true)"
if [[ -n "${ble_name}" ]]; then
  # Evita que el escaneo muestre el hostname (p. ej. "cardmed") en lugar de NiloCardmed-<uuid>.
  bluetoothctl system-alias "${ble_name}" 2>/dev/null || true
fi

for _ in $(seq 1 10); do
  if bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
    if ! bluetoothctl show 2>/dev/null | grep -q "Discoverable: yes"; then
      bluetoothctl discoverable on 2>/dev/null || true
      bluetoothctl pairable on 2>/dev/null || true
    fi
    if [[ -n "${ble_name}" ]]; then
      echo "[nilocardmed] Bluetooth host: Powered=yes, discoverable=on, alias=${ble_name}"
    else
      echo "[nilocardmed] Bluetooth host: Powered=yes, discoverable=on"
    fi
    if ! bluetoothctl show 2>/dev/null | grep -q "Discoverable: yes"; then
      echo "[nilocardmed][AVISO] Discoverable sigue en 'no' — revisa BlueZ y LE advertisement" >&2
    fi
    exit 0
  fi
  sleep 1
done

echo "[nilocardmed][ERROR] Adaptador Bluetooth no encendido (bluetoothctl show → Powered: no)" >&2
echo "[nilocardmed][ERROR] Prueba: sudo rfkill unblock bluetooth && sudo bluetoothctl power on" >&2
exit 1
