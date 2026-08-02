#!/usr/bin/env bash
# Genera docker-compose.override.yml según flags de deploy.env (USB, Bluetooth, privileged).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export INSTALL_DIR="${INSTALL_DIR:-${REPO_ROOT}}"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

load_deploy_env

override_file="${INSTALL_DIR}/docker-compose.override.yml"
tmp_file="$(mktemp)"

log_info "Generando ${override_file}"

{
  echo "# Generado por scripts/generate-compose-override.sh - no editar a mano."
  echo "services:"
  echo "  ${COMPOSE_SERVICE_NAME:-nilocardmed}:"

  devices=()
  volumes=()

  if is_true "${MOUNT_USB_BUS:-true}"; then
    devices+=("/dev/bus/usb:/dev/bus/usb")
  fi

  if is_true "${ENABLE_BLUETOOTH:-false}"; then
    dbus_path="${BLUETOOTH_DBUS_SYSTEM_PATH:-/var/run/dbus/system_bus_socket}"
    volumes+=("${dbus_path}:/var/run/dbus/system_bus_socket:ro")
  fi

  if is_true "${ENABLE_WIFI:-false}"; then
    wifi_script_host="${WIFI_HOST_SCRIPT_HOST:-${INSTALL_DIR}/scripts/wifi-host.sh}"
    wifi_script_container="${WIFI_HOST_SCRIPT_CONTAINER:-/host/scripts/wifi-host.sh}"
    volumes+=("${wifi_script_host}:${wifi_script_container}:ro")
    dbus_path="${WIFI_DBUS_SYSTEM_PATH:-/var/run/dbus/system_bus_socket}"
    volumes+=("${dbus_path}:/var/run/dbus/system_bus_socket:ro")
  fi

  if ((${#devices[@]} > 0)); then
    echo "    devices:"
    for device in "${devices[@]}"; do
      echo "      - ${device}"
    done
  fi

  if ((${#volumes[@]} > 0)); then
    echo "    volumes:"
    for volume in "${volumes[@]}"; do
      echo "      - ${volume}"
    done
  fi

  if is_true "${PRIVILEGED_MODE:-false}"; then
    echo "    privileged: true"
  fi

  if is_true "${ENABLE_WIFI:-false}" || is_true "${ENABLE_BLUETOOTH:-false}"; then
    echo "    network_mode: host"
  fi
} > "${tmp_file}"

if [[ "$(wc -l < "${tmp_file}")" -le 3 ]]; then
  log_info "Sin overrides de hardware; omitiendo ${override_file}"
  rm -f "${override_file}" "${tmp_file}"
  exit 0
fi

mv "${tmp_file}" "${override_file}"
log_info "Override generado correctamente"
