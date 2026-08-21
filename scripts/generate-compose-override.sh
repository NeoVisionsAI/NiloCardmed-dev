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

resolve_video_device_path() {
  local configured="${VIDEO_DEVICE_HOST:-/dev/video0}"
  if [[ -e "${configured}" ]]; then
    echo "${configured}"
    return 0
  fi
  local candidate
  for candidate in /dev/video*; do
    [[ -e "${candidate}" ]] || continue
    echo "${candidate}"
    return 0
  done
  return 1
}

log_info "Generando ${override_file}"

devices=()
volumes=()
camera_hotplug=false

if is_true "${ENABLE_CAMERA_HOTPLUG:-true}"; then
  camera_hotplug=true
  log_info "Cámara USB hot-plug: /dev del host (conectar/desconectar sin reiniciar el contenedor)"
else
  video_host=""
  video_container=""
  if video_host="$(resolve_video_device_path)"; then
    video_container="${VIDEO_DEVICE_CONTAINER:-/dev/video0}"
    devices+=("${video_host}:${video_container}")
    log_info "Cámara estática: ${video_host} → ${video_container}"
  elif is_true "${VIDEO_DEVICE_REQUIRED:-false}"; then
    log_error "Cámara requerida (VIDEO_DEVICE_REQUIRED=true) pero no se encontró ${VIDEO_DEVICE_HOST:-/dev/video0}"
    exit 1
  else
    log_info "Cámara no detectada — contenedor sin dispositivo de vídeo (modo estático)"
  fi

  if is_true "${MOUNT_USB_BUS:-true}"; then
    devices+=("/dev/bus/usb:/dev/bus/usb")
  fi
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

extra_group_gids=()
primary_group="${CONTAINER_GROUP_ADD:-video}"
primary_gid=""
if primary_gid="$(resolve_host_group_gid "${primary_group}")"; then
  extra_group_gids+=("${primary_gid}")
  log_info "Contenedor: grupo ${primary_group} → GID ${primary_gid}"
else
  log_warn "Grupo ${primary_group} no disponible; omitiendo group_add para cámara"
fi

if is_true "${ENABLE_BLUETOOTH:-false}"; then
  bt_gid=""
  if bt_gid="$(resolve_host_group_gid bluetooth)"; then
    extra_group_gids+=("${bt_gid}")
    log_info "Contenedor: grupo bluetooth → GID ${bt_gid}"
  else
    log_warn "Grupo bluetooth no disponible; BLE usará solo D-Bus (sin group_add)"
  fi
fi

{
  echo "# Generado por scripts/generate-compose-override.sh - no editar a mano."
  echo "services:"
  echo "  ${COMPOSE_SERVICE_NAME:-nilocardmed}:"

  if ((${#devices[@]} > 0)); then
    echo "    devices:"
    for device in "${devices[@]}"; do
      echo "      - ${device}"
    done
  fi

  if [[ "${camera_hotplug}" == true ]] || ((${#volumes[@]} > 0)); then
    echo "    volumes:"
    if [[ "${camera_hotplug}" == true ]]; then
      echo "      - type: bind"
      echo "        source: /dev"
      echo "        target: /dev"
      echo "        bind:"
      echo "          propagation: rslave"
    fi
    for volume in "${volumes[@]}"; do
      echo "      - ${volume}"
    done
  fi

  if is_true "${PRIVILEGED_MODE:-false}"; then
    echo "    privileged: true"
  fi

  if ((${#extra_group_gids[@]} > 0)); then
    echo "    group_add:"
    for gid in "${extra_group_gids[@]}"; do
      echo "      - \"${gid}\""
    done
  fi

  if is_true "${ENABLE_WIFI:-false}" || is_true "${ENABLE_BLUETOOTH:-false}"; then
    echo "    network_mode: host"
  fi
} > "${tmp_file}"

if [[ "$(wc -l < "${tmp_file}")" -le 3 ]]; then
  log_info "Sin overrides de hardware; omitiendo ${override_file}"
  rm -f "${override_file}" "${tmp_file}"
  # shellcheck source=lib/setup-env.sh
  source "${SCRIPT_DIR}/lib/setup-env.sh"
  sync_compose_file_env "${INSTALL_DIR}"
  ensure_install_dir_permissions "${INSTALL_DIR}"
  exit 0
fi

mv "${tmp_file}" "${override_file}"
log_info "Override generado correctamente"
# shellcheck source=lib/setup-env.sh
source "${SCRIPT_DIR}/lib/setup-env.sh"
sync_compose_file_env "${INSTALL_DIR}"
ensure_install_dir_permissions "${INSTALL_DIR}"
