#!/usr/bin/env bash
# Generación de deploy.env / .env en instalación (UUID BLE persistente, contraseña).

set -euo pipefail

# shellcheck source=common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

update_env_file() {
  local file="$1"
  local key="$2"
  local value="$3"
  python3 - "$file" "$key" "$value" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
out: list[str] = []
found = False
for line in lines:
    if line.startswith(f"{key}="):
        if " " in value or '"' in value or "'" in value:
            out.append(f'{key}="{value.replace(chr(34), "")}"')
        else:
            out.append(f"{key}={value}")
        found = True
    else:
        out.append(line)
if not found:
    if " " in value or '"' in value or "'" in value:
        out.append(f'{key}="{value.replace(chr(34), "")}"')
    else:
        out.append(f"{key}={value}")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

read_env_value() {
  local file="$1"
  local key="$2"
  if [[ ! -f "${file}" ]]; then
    return 1
  fi
  grep -E "^${key}=" "${file}" 2>/dev/null | tail -1 | cut -d= -f2- || return 1
}

generate_device_uuid() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen | tr '[:upper:]' '[:lower:]' | tr -d '-' | cut -c1-8
  else
    tr -d '-' </proc/sys/kernel/random/uuid | cut -c1-8
  fi
}

generate_bluetooth_password() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 8
  else
    tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16
  fi
}

load_or_create_device_identity() {
  local data_dir="$1"
  local identity_file="${data_dir}/device-identity.env"

  mkdir -p "${data_dir}"

  if [[ -f "${identity_file}" ]]; then
    # shellcheck disable=SC1090
    set -a && source "${identity_file}" && set +a
    if [[ -n "${DEVICE_UUID:-}" ]]; then
      BLE_DEVICE_NAME="${BLE_DEVICE_NAME:-NiloCardmed-${DEVICE_UUID}}"
      log_info "Identidad existente: ${BLE_DEVICE_NAME} (${identity_file})"
      export DEVICE_UUID BLE_DEVICE_NAME
      return 0
    fi
  fi

  DEVICE_UUID="$(generate_device_uuid)"
  BLE_DEVICE_NAME="NiloCardmed-${DEVICE_UUID}"
  cat >"${identity_file}" <<EOF
# Identidad persistente del dispositivo (generada por install.sh)
# DEVICE_UUID → nombre BLE (NiloCardmed-<uuid>) y NILOCARDMED_SER__DEVICE_ID en peticiones
DEVICE_UUID=${DEVICE_UUID}
BLE_DEVICE_NAME=${BLE_DEVICE_NAME}
EOF
  chmod 600 "${identity_file}" 2>/dev/null || true
  log_info "Nueva identidad BLE: ${BLE_DEVICE_NAME}"
  log_info "Guardada en ${identity_file}"
  export DEVICE_UUID BLE_DEVICE_NAME
}

prompt_bluetooth_password() {
  local env_file="$1"
  local existing=""
  existing="$(read_env_value "${env_file}" "NILOCARDMED_BLUETOOTH__PASSWORD" || true)"

  local password=""
  local confirm=""
  if [[ -t 0 ]] || [[ -r /dev/tty ]]; then
    if [[ -n "${existing}" && "${existing}" != "changeme" ]]; then
      read -rsp "Contraseña Bluetooth [Enter=mantener la actual]: " password </dev/tty || true
      echo >/dev/tty
      if [[ -z "${password}" ]]; then
        password="${existing}"
        log_info "Contraseña Bluetooth: se mantiene la existente"
        export BLUETOOTH_PASSWORD="${password}"
        return 0
      fi
    else
      read -rsp "Contraseña Bluetooth [Enter=generar automática]: " password </dev/tty || true
      echo >/dev/tty
      if [[ -z "${password}" ]]; then
        password="$(generate_bluetooth_password)"
        log_info "Contraseña Bluetooth generada: ${password}"
        log_info "Guárdala: la necesitarás en la app tablet."
        export BLUETOOTH_PASSWORD="${password}"
        return 0
      fi
    fi

    while true; do
      read -rsp "Repite la contraseña Bluetooth: " confirm </dev/tty || true
      echo >/dev/tty
      if [[ "${password}" == "${confirm}" ]]; then
        break
      fi
      log_error "Las contraseñas no coinciden. Vuelve a intentarlo."
      while [[ -z "${password}" ]]; do
        read -rsp "Contraseña Bluetooth: " password </dev/tty || true
        echo >/dev/tty
        if [[ -z "${password}" ]]; then
          log_error "La contraseña no puede estar vacía (Enter solo en el primer prompt para generar o mantener)."
        fi
      done
    done
  fi

  if [[ -z "${password}" ]]; then
    password="$(generate_bluetooth_password)"
    log_info "Contraseña Bluetooth generada: ${password}"
    log_info "Guárdala: la necesitarás en la app tablet."
  fi

  export BLUETOOTH_PASSWORD="${password}"
}

sync_deploy_run_user() {
  local deploy_file="$1"
  local current_user current_group

  resolve_run_user "${SUDO_USER:-root}"
  current_user="$(read_env_value "${deploy_file}" "NILOCARDMED_RUN_USER" || true)"
  current_group="$(read_env_value "${deploy_file}" "NILOCARDMED_RUN_GROUP" || true)"

  if [[ "${current_user}" != "${NILOCARDMED_RUN_USER}" ]]; then
    update_env_file "${deploy_file}" "NILOCARDMED_RUN_USER" "${NILOCARDMED_RUN_USER}"
    log_info "deploy.env: NILOCARDMED_RUN_USER=${NILOCARDMED_RUN_USER}"
  fi
  if [[ "${current_group}" != "${NILOCARDMED_RUN_GROUP}" ]]; then
    update_env_file "${deploy_file}" "NILOCARDMED_RUN_GROUP" "${NILOCARDMED_RUN_GROUP}"
    log_info "deploy.env: NILOCARDMED_RUN_GROUP=${NILOCARDMED_RUN_GROUP}"
  fi
}

ensure_deploy_production_flags() {
  local deploy_file="$1"

  update_env_file "${deploy_file}" "ENABLE_WIFI" "true"
  update_env_file "${deploy_file}" "ENABLE_BLUETOOTH" "true"
  update_env_file "${deploy_file}" "ENABLE_CAMERA_HOTPLUG" "true"
  update_env_file "${deploy_file}" "MOUNT_USB_BUS" "true"
  update_env_file "${deploy_file}" "VIDEO_DEVICE_REQUIRED" "false"
  update_env_file "${deploy_file}" "DOCKER_DEFAULT_PLATFORM" "linux/arm/v7"

  if command -v docker >/dev/null 2>&1; then
    local docker_bin
    docker_bin="$(command -v docker)"
    update_env_file "${deploy_file}" "DOCKER_COMPOSE_CMD" "${docker_bin} compose"
  fi

  log_info "deploy.env: flags de producción aplicados (WiFi, BLE, hot-plug cámara)"
}

ensure_app_production_flags() {
  local env_file="$1"

  update_env_file "${env_file}" "NILOCARDMED_BLUETOOTH__ENABLED" "true"
  update_env_file "${env_file}" "NILOCARDMED_BLUETOOTH__BACKEND" "bluez"
  update_env_file "${env_file}" "NILOCARDMED_WIFI__ENABLED" "true"
  log_info ".env: BLE backend=bluez, WiFi habilitado"
}

migrate_deploy_service_name_var() {
  local deploy_file="$1"
  local legacy new_name

  legacy="$(read_env_value "${deploy_file}" "NILOCARDMED_SERVICE_NAME" || true)"
  new_name="$(read_env_value "${deploy_file}" "SYSTEMD_UNIT_NAME" || true)"
  if [[ -z "${new_name}" && -n "${legacy}" ]]; then
    update_env_file "${deploy_file}" "SYSTEMD_UNIT_NAME" "${legacy}"
    log_info "deploy.env: SYSTEMD_UNIT_NAME=${legacy} (migrado desde NILOCARDMED_SERVICE_NAME)"
  fi
}

sync_compose_file_env() {
  local install_dir="$1"
  local deploy_file="${install_dir}/deploy.env"
  local override_file="${install_dir}/docker-compose.override.yml"
  local base="${COMPOSE_FILE:-docker-compose.yml:docker-compose.pi.yml}"

  base="${base//:docker-compose.override.yml/}"
  base="${base//docker-compose.override.yml:/}"
  base="${base//docker-compose.override.yml/}"

  if [[ -f "${override_file}" ]]; then
    update_env_file "${deploy_file}" "COMPOSE_FILE" "${base}:docker-compose.override.yml"
    log_info "deploy.env: COMPOSE_FILE incluye docker-compose.override.yml (systemd + BLE/WiFi)"
  else
    update_env_file "${deploy_file}" "COMPOSE_FILE" "${base}"
  fi
}

setup_deploy_and_app_env() {
  local install_dir="$1"
  local deploy_example="${install_dir}/deploy.env.example"
  local deploy_file="${install_dir}/deploy.env"
  local env_example="${install_dir}/.env.example"
  local env_file="${install_dir}/.env"

  if [[ ! -f "${deploy_example}" ]]; then
    log_error "No se encontró ${deploy_example}"
    exit 1
  fi
  if [[ ! -f "${env_example}" ]]; then
    log_error "No se encontró ${env_example}"
    exit 1
  fi

  if [[ ! -f "${deploy_file}" ]]; then
    log_info "Creando ${deploy_file} desde deploy.env.example"
    cp "${deploy_example}" "${deploy_file}"
  else
    log_info "Usando ${deploy_file} existente"
  fi

  # shellcheck disable=SC1090
  set -a && source "${deploy_file}" && set +a
  sync_deploy_run_user "${deploy_file}"
  migrate_deploy_service_name_var "${deploy_file}"
  ensure_deploy_production_flags "${deploy_file}"
  # shellcheck disable=SC1090
  set -a && source "${deploy_file}" && set +a
  sync_compose_file_env "${install_dir}"
  # shellcheck disable=SC1090
  set -a && source "${deploy_file}" && set +a
  local data_dir="${HOST_DATA_DIR:-/var/lib/nilocardmed/data}"

  if [[ ! -f "${env_file}" ]]; then
    log_info "Creando ${env_file} desde .env.example"
    cp "${env_example}" "${env_file}"
  else
    log_info "Usando ${env_file} existente"
  fi

  load_or_create_device_identity "${data_dir}"
  prompt_bluetooth_password "${env_file}"

  update_env_file "${env_file}" "NILOCARDMED_BLUETOOTH__DEVICE_NAME" "${BLE_DEVICE_NAME}"
  update_env_file "${env_file}" "NILOCARDMED_BLUETOOTH__PASSWORD" "${BLUETOOTH_PASSWORD}"
  update_env_file "${env_file}" "NILOCARDMED_SER__DEVICE_ID" "${DEVICE_UUID}"
  ensure_app_production_flags "${env_file}"

  log_info "Configuración aplicada:"
  log_info "  device_id (SER)=${DEVICE_UUID}"
  log_info "  BLE device_name=${BLE_DEVICE_NAME}"
  log_info "  BLE password=(configurada en .env)"
  if [[ "${EUID}" -eq 0 ]]; then
    ensure_install_dir_permissions "${install_dir}"
  fi
}
