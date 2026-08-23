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

generate_connection_password() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 8
  else
    tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16
  fi
}

# Lee un secreto desde /dev/tty. Con timeout > 0, caduca y deja el valor vacío.
read_secret_from_tty() {
  local prompt="$1"
  local timeout_seconds="${2:-0}"
  local __var_name="$3"
  local value=""
  local rc=0

  # No usar -rsp junto: -p consumiría el siguiente argumento (-t) como prompt.
  if [[ "${timeout_seconds}" -gt 0 ]]; then
    if read -r -s -t "${timeout_seconds}" -p "${prompt}" value </dev/tty; then
      rc=0
    else
      rc=$?
      value=""
    fi
  else
    if read -r -s -p "${prompt}" value </dev/tty; then
      rc=0
    else
      rc=$?
      value=""
    fi
  fi
  echo >/dev/tty
  printf -v "${__var_name}" '%s' "${value}"
  return "${rc}"
}

has_existing_connection_password() {
  local existing="$1"
  connection_password_is_valid "${existing}"
}

connection_password_is_valid() {
  local existing="$1"
  [[ -n "${existing}" && "${existing}" != "changeme" && ${#existing} -ge 8 && ${#existing} -le 63 \
    && "${existing}" != *"#"* && "${existing}" != *$'\n'* && "${existing}" != *$'\r'* ]]
}

read_existing_connection_password() {
  local env_file="$1"
  local existing=""
  existing="$(read_env_value "${env_file}" "NILOCARDMED_CONNECTION_PASSWORD" || true)"
  if has_existing_connection_password "${existing}"; then
    echo "${existing}"
    return 0
  fi
  existing="$(read_env_value "${env_file}" "NILOCARDMED_BLUETOOTH__PASSWORD" || true)"
  if has_existing_connection_password "${existing}"; then
    echo "${existing}"
    return 0
  fi
  return 1
}

# Copia BLE legacy → CONNECTION_PASSWORD si falta o es demasiado corta para WPA2.
migrate_legacy_connection_password() {
  local env_file="$1"
  local current legacy

  current="$(read_env_value "${env_file}" "NILOCARDMED_CONNECTION_PASSWORD" || true)"
  if connection_password_is_valid "${current}"; then
    return 0
  fi

  legacy="$(read_env_value "${env_file}" "NILOCARDMED_BLUETOOTH__PASSWORD" || true)"
  if connection_password_is_valid "${legacy}"; then
    log_info "Migrando NILOCARDMED_BLUETOOTH__PASSWORD → NILOCARDMED_CONNECTION_PASSWORD (WPA2)"
    update_env_file "${env_file}" "NILOCARDMED_CONNECTION_PASSWORD" "${legacy}"
  elif [[ -n "${current}" || -n "${legacy}" ]]; then
    log_warn "Contraseña existente demasiado corta para WPA2 (mín. 8) — se pedirá una nueva"
  fi
}

read_raw_connection_password() {
  local env_file="$1"
  local raw=""

  raw="$(read_env_value "${env_file}" "NILOCARDMED_CONNECTION_PASSWORD" || true)"
  if [[ -n "${raw}" ]]; then
    echo "${raw}"
    return 0
  fi
  raw="$(read_env_value "${env_file}" "NILOCARDMED_BLUETOOTH__PASSWORD" || true)"
  if [[ -n "${raw}" ]]; then
    echo "${raw}"
    return 0
  fi
  return 1
}

report_invalid_connection_password() {
  local pwd="$1"

  if [[ -z "${pwd}" ]]; then
    echo "[nilocardmed][ERROR] La contraseña no puede estar vacía." >/dev/tty
  elif [[ "${pwd}" == "changeme" ]]; then
    echo "[nilocardmed][ERROR] 'changeme' no es válida; elige otra contraseña." >/dev/tty
  elif [[ ${#pwd} -lt 8 ]]; then
    echo "[nilocardmed][ERROR] Mínimo 8 caracteres (WPA2); has introducido ${#pwd}." >/dev/tty
  elif [[ ${#pwd} -gt 63 ]]; then
    echo "[nilocardmed][ERROR] Máximo 63 caracteres (WPA2)." >/dev/tty
  elif [[ "${pwd}" == *"#"* ]]; then
    echo "[nilocardmed][ERROR] No puede contener '#' (limitación del AP WiFi)." >/dev/tty
  else
    echo "[nilocardmed][ERROR] Contraseña no válida para WPA2." >/dev/tty
  fi
}

# Pide contraseña por TTY hasta que cumpla WPA2 (8-63, no changeme). Sin timeout.
prompt_valid_password_from_tty() {
  local prompt="$1"
  local password=""

  while ! connection_password_is_valid "${password}"; do
    if [[ -n "${password}" ]]; then
      report_invalid_connection_password "${password}"
    fi
    password=""
    read_secret_from_tty "${prompt}" 0 password || true
  done
  echo "${password}"
}

confirm_connection_password_match() {
  local __out_var="$1"
  local password="$2"
  local confirm=""

  while true; do
    read_secret_from_tty "Repite la contraseña: " 0 confirm || true
    if [[ "${password}" == "${confirm}" ]]; then
      printf -v "${__out_var}" '%s' "${password}"
      return 0
    fi
    echo "[nilocardmed][ERROR] No coinciden. Vuelve a intentarlo." >/dev/tty
    password="$(prompt_valid_password_from_tty "Contraseña: ")"
  done
}

prompt_connection_password() {
  local env_file="$1"
  local raw_existing valid_existing=""
  local password=""
  local kept_existing=false

  raw_existing="$(read_raw_connection_password "${env_file}" || true)"
  valid_existing="$(read_existing_connection_password "${env_file}" || true)"

  if [[ ! -r /dev/tty ]]; then
    log_warn "Sin TTY (/dev/tty) — no se puede pedir contraseña de forma interactiva"
    if connection_password_is_valid "${valid_existing}"; then
      password="${valid_existing}"
      log_info "Contraseña de aprovisionamiento: se mantiene la existente (sin TTY)"
    else
      password="$(generate_connection_password)"
      log_info "Contraseña de aprovisionamiento generada: ${password}"
      log_info "Guárdala: WiFi AP + auth HTTP en la tablet."
    fi
    if ! connection_password_is_valid "${password}"; then
      log_error "Contraseña inválida tras setup (8-63 caracteres, no 'changeme')"
      return 1
    fi
    export CONNECTION_PASSWORD="${password}"
    return 0
  fi

  echo "" >/dev/tty
  echo "[nilocardmed] === Contraseña de aprovisionamiento (WiFi AP + HTTP) ===" >/dev/tty
  echo "[nilocardmed] Usada para: WPA del AP Nilocardmed-Config-xxxx y comando auth HTTP." >/dev/tty
  echo "[nilocardmed] Requisitos: 8-63 caracteres, distinta de 'changeme'." >/dev/tty
  echo "" >/dev/tty

  if connection_password_is_valid "${valid_existing}"; then
    read_secret_from_tty \
      "Nueva contraseña [Enter o espera 10 s = mantener la actual]: " \
      10 \
      password || true
    if [[ -z "${password}" ]]; then
      password="${valid_existing}"
      kept_existing=true
      echo "[nilocardmed] Contraseña: se mantiene la existente." >/dev/tty
    elif ! connection_password_is_valid "${password}"; then
      report_invalid_connection_password "${password}"
      password="$(prompt_valid_password_from_tty "Contraseña aprovisionamiento [mín. 8 caracteres]: ")"
    fi
  else
    if [[ -n "${raw_existing}" ]]; then
      echo "[nilocardmed] La contraseña guardada no vale para WPA2. Debes introducir una nueva (no hay opción de mantenerla)." >/dev/tty
    fi
    password="$(prompt_valid_password_from_tty "Contraseña aprovisionamiento [mín. 8 caracteres]: ")"
  fi

  if [[ "${kept_existing}" != true ]]; then
    confirm_connection_password_match password "${password}"
    echo "[nilocardmed] Contraseña de aprovisionamiento actualizada." >/dev/tty
  fi

  if ! connection_password_is_valid "${password}"; then
    log_error "Contraseña inválida; no se continuará con la instalación/actualización."
    return 1
  fi

  export CONNECTION_PASSWORD="${password}"
  return 0
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
  update_env_file "${deploy_file}" "ENABLE_BLUETOOTH" "false"
  update_env_file "${deploy_file}" "BLUETOOTH_ENABLED" "false"
  update_env_file "${deploy_file}" "ENABLE_WIFI_AP" "true"
  update_env_file "${deploy_file}" "ENABLE_CAMERA_HOTPLUG" "true"
  update_env_file "${deploy_file}" "MOUNT_USB_BUS" "true"
  update_env_file "${deploy_file}" "VIDEO_DEVICE_REQUIRED" "false"
  update_env_file "${deploy_file}" "DOCKER_DEFAULT_PLATFORM" "linux/arm/v7"

  if command -v docker >/dev/null 2>&1; then
    local docker_bin
    docker_bin="$(command -v docker)"
    update_env_file "${deploy_file}" "DOCKER_COMPOSE_CMD" "${docker_bin} compose"
  fi

  log_info "deploy.env: flags de producción aplicados (WiFi AP, HTTP local; BLE legacy off)"
  if [[ -z "$(read_env_value "${deploy_file}" "DISABLE_GUI" || true)" ]]; then
    update_env_file "${deploy_file}" "DISABLE_GUI" "false"
  fi
  if [[ -z "$(read_env_value "${deploy_file}" "OPTIMIZE_GPU_MEM" || true)" ]]; then
    update_env_file "${deploy_file}" "OPTIMIZE_GPU_MEM" "true"
  fi
}

ensure_app_production_flags() {
  local env_file="$1"

  update_env_file "${env_file}" "NILOCARDMED_BLUETOOTH__ENABLED" "false"
  update_env_file "${env_file}" "NILOCARDMED_BLUETOOTH__BACKEND" "bluez"
  update_env_file "${env_file}" "NILOCARDMED_WIFI__ENABLED" "true"
  update_env_file "${env_file}" "NILOCARDMED_HTTP__ENABLED" "true"
  update_env_file "${env_file}" "NILOCARDMED_HTTP__BIND_AP_ONLY" "false"
  update_env_file "${env_file}" "NILOCARDMED_BLUETOOTH__CAPTURE_TEST_MODE" "base64"
  update_env_file "${env_file}" "NILOCARDMED_BLUETOOTH__MAX_IMAGE_RESPONSE_BYTES" "524288"
  log_info ".env: WiFi + HTTP local habilitados; BLE desactivado (código conservado)"
}

sync_wifi_ap_password_from_env() {
  local deploy_file="$1"
  local env_file="$2"
  local pwd=""

  pwd="$(read_env_value "${env_file}" "NILOCARDMED_CONNECTION_PASSWORD" || true)"
  if ! connection_password_is_valid "${pwd}"; then
    pwd="$(read_env_value "${env_file}" "NILOCARDMED_BLUETOOTH__PASSWORD" || true)"
  fi
  if connection_password_is_valid "${pwd}"; then
    update_env_file "${deploy_file}" "WIFI_AP_PASSWORD" "${pwd}"
    log_info "deploy.env: WIFI_AP_PASSWORD sincronizada (WPA2 en AP)"
  else
    log_warn "Sin contraseña WPA2 válida en .env — configura NILOCARDMED_CONNECTION_PASSWORD (8-63 chars)"
  fi
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

# Imagen actual acepta CSV (200,201) y JSON ([200,201]); normalizar a CSV legible.
normalize_env_list_values() {
  local env_file="$1"
  python3 - "$env_file" <<'PY'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    sys.exit(0)

keys = {
    "NILOCARDMED_SER__SUCCESS_STATUS_CODES": "int",
    "NILOCARDMED_SER__RETRY_ON_STATUS_CODES": "int",
    "NILOCARDMED_BLUETOOTH__ALLOWED_COMMANDS_WITHOUT_AUTH": "str",
}

lines = path.read_text(encoding="utf-8").splitlines()
out: list[str] = []
changed = False

for line in lines:
    matched = False
    for key, kind in keys.items():
        prefix = f"{key}="
        if not line.startswith(prefix):
            continue
        matched = True
        raw = line[len(prefix) :].strip().strip('"')
        if not raw.startswith("["):
            out.append(line)
            break
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            out.append(line)
            break
        if not isinstance(parsed, list):
            out.append(line)
            break
        if kind == "int":
            normalized = ",".join(str(int(x)) for x in parsed)
        else:
            normalized = ",".join(str(x) for x in parsed)
        out.append(f"{key}={normalized}")
        changed = True
        break
    if not matched:
        out.append(line)

if changed:
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("[nilocardmed] .env: listas normalizadas a formato CSV")
PY
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
  migrate_legacy_connection_password "${env_file}"
  if ! prompt_connection_password "${env_file}"; then
    log_error "Actualización cancelada: contraseña WPA2 obligatoria (8-63 caracteres, no 'changeme')."
    exit 1
  fi

  if ! connection_password_is_valid "${CONNECTION_PASSWORD:-}"; then
    log_error "Contraseña inválida tras setup (8-63 caracteres, no 'changeme')"
    exit 1
  fi

  update_env_file "${env_file}" "NILOCARDMED_BLUETOOTH__DEVICE_NAME" "${BLE_DEVICE_NAME}"
  update_env_file "${env_file}" "NILOCARDMED_CONNECTION_PASSWORD" "${CONNECTION_PASSWORD}"
  update_env_file "${deploy_file}" "WIFI_AP_PASSWORD" "${CONNECTION_PASSWORD}"
  update_env_file "${env_file}" "NILOCARDMED_SER__DEVICE_ID" "${DEVICE_UUID}"
  ensure_app_production_flags "${env_file}"
  sync_wifi_ap_password_from_env "${deploy_file}" "${env_file}"
  normalize_env_list_values "${env_file}"

  log_info "Configuración aplicada:"
  log_info "  device_id (SER)=${DEVICE_UUID}"
  log_info "  device_name=${BLE_DEVICE_NAME}"
  log_info "  connection_password=(configurada en .env como NILOCARDMED_CONNECTION_PASSWORD)"
  if [[ "${EUID}" -eq 0 ]]; then
    ensure_install_dir_permissions "${install_dir}"
    # El reinicio del AP se hace una sola vez al final de install.sh (evita 2–3 wait-ready seguidos).
  fi
}
