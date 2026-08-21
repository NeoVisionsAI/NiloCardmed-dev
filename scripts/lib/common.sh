#!/usr/bin/env bash

set -euo pipefail

log_info() {
  echo "[nilocardmed] $*"
}

log_error() {
  echo "[nilocardmed][ERROR] $*" >&2
}

log_warn() {
  echo "[nilocardmed][AVISO] $*" >&2
}

is_true() {
  case "${1,,}" in
    1 | true | yes | on) return 0 ;;
    *) return 1 ;;
  esac
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log_error "Comando requerido no encontrado: $1"
    exit 1
  fi
}

# Directorio de instalación: INSTALL_DIR explícito, o NILOCARDMED_INSTALL_DIR del deploy, o repo.
resolve_install_dir_from_repo() {
  local repo_root="$1"
  local candidate="${repo_root}"

  if [[ -n "${INSTALL_DIR:-}" ]]; then
    printf '%s\n' "${INSTALL_DIR}"
    return 0
  fi

  if [[ -f "${repo_root}/deploy.env" ]]; then
    # shellcheck disable=SC1091
    set -a && source "${repo_root}/deploy.env" && set +a
    candidate="${NILOCARDMED_INSTALL_DIR:-${repo_root}}"
  elif [[ -f "${repo_root}/deploy.env.example" ]]; then
    # shellcheck disable=SC1091
    set -a && source "${repo_root}/deploy.env.example" && set +a
    candidate="${NILOCARDMED_INSTALL_DIR:-${repo_root}}"
  fi

  printf '%s\n' "${candidate}"
}

sync_project_to_install_dir() {
  local src="$1"
  local dst="$2"

  if [[ "$(realpath -m "${src}")" == "$(realpath -m "${dst}")" ]]; then
    log_info "Instalación in-place en ${dst}"
    ensure_scripts_executable "${dst}"
    return 0
  fi

  log_info "Sincronizando ${src} → ${dst}"
  mkdir -p "${dst}"
  rsync -a --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude 'data' \
    --exclude '__pycache__' \
    --exclude 'deploy.env' \
    --exclude '.env' \
    "${src}/" "${dst}/"
  ensure_scripts_executable "${dst}"
  if [[ "${EUID}" -eq 0 ]]; then
    ensure_install_dir_permissions "${dst}"
  fi
}

ensure_scripts_executable() {
  local install_dir="$1"
  chmod +x "${install_dir}/scripts/"*.sh 2>/dev/null || true
  chmod +x "${install_dir}/scripts/lib/"*.sh 2>/dev/null || true
}

# systemd ejecuta docker compose como NILOCARDMED_RUN_USER; archivos creados por install (root)
# deben ser legibles por ese usuario (override, deploy.env, compose…).
ensure_install_dir_permissions() {
  local install_dir="$1"
  resolve_run_user "${SUDO_USER:-$(id -un)}"
  local user="${NILOCARDMED_RUN_USER}"
  local group="${NILOCARDMED_RUN_GROUP}"

  if [[ "${EUID}" -ne 0 ]]; then
    chmod 644 "${install_dir}/docker-compose.override.yml" 2>/dev/null || true
    return 0
  fi

  log_info "Permisos: ${user}:${group} en ${install_dir}"
  chown -R "${user}:${group}" "${install_dir}"
  find "${install_dir}" -type d -exec chmod 755 {} +
  find "${install_dir}" -type f -exec chmod 644 {} +
  ensure_scripts_executable "${install_dir}"
  chmod 640 "${install_dir}/deploy.env" "${install_dir}/.env" 2>/dev/null || true
  chmod 644 "${install_dir}"/docker-compose*.yml 2>/dev/null || true
}

ensure_group_exists() {
  local group="$1"

  if getent group "${group}" >/dev/null 2>&1; then
    return 0
  fi

  if [[ "${EUID}" -ne 0 ]]; then
    log_warn "Grupo '${group}' no existe en el host (necesitas sudo install.sh)"
    return 1
  fi

  case "${group}" in
    docker | bluetooth | dialout | plugdev | video)
      log_info "Creando grupo del sistema: ${group}"
      if ! groupadd -r "${group}" 2>/dev/null && ! groupadd "${group}" 2>/dev/null; then
        log_warn "No se pudo crear el grupo ${group}"
        return 1
      fi
      ;;
    *)
      log_warn "Grupo desconocido omitido: ${group}"
      return 1
      ;;
  esac
}

resolve_host_group_gid() {
  local group="$1"
  local gid=""

  ensure_group_exists "${group}" || true
  if ! getent group "${group}" >/dev/null 2>&1; then
    return 1
  fi
  gid="$(getent group "${group}" | cut -d: -f3)"
  if [[ -z "${gid}" ]]; then
    return 1
  fi
  printf '%s\n' "${gid}"
}

load_deploy_env() {
  local repo_root
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  local preset_install_dir="${INSTALL_DIR:-}"

  INSTALL_DIR="${preset_install_dir:-${repo_root}}"
  DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-${INSTALL_DIR}/deploy.env}"

  if [[ -f "${DEPLOY_ENV_FILE}" ]]; then
    # shellcheck disable=SC1090
    set -a
    source "${DEPLOY_ENV_FILE}"
    set +a
  fi

  if [[ -z "${preset_install_dir}" ]]; then
    INSTALL_DIR="${NILOCARDMED_INSTALL_DIR:-${repo_root}}"
    DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-${INSTALL_DIR}/deploy.env}"
  fi

  NILOCARDMED_SERVICE_NAME="${SYSTEMD_UNIT_NAME:-${NILOCARDMED_SERVICE_NAME:-nilocardmed}}"
  NILOCARDMED_RUN_USER="${NILOCARDMED_RUN_USER:-${SUDO_USER:-$(id -un)}}"
  NILOCARDMED_RUN_GROUP="${NILOCARDMED_RUN_GROUP:-${NILOCARDMED_RUN_USER}}"
  DOCKER_COMPOSE_CMD="${DOCKER_COMPOSE_CMD:-docker compose}"
  COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml:docker-compose.pi.yml}"
  COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-nilocardmed}"
  COMPOSE_SERVICE_NAME="${COMPOSE_SERVICE_NAME:-nilocardmed}"
  SYSTEMD_WANTED_BY="${SYSTEMD_WANTED_BY:-multi-user.target}"
  SYSTEMD_AFTER="${SYSTEMD_AFTER:-docker.service network-online.target}"
  SYSTEMD_REQUIRES="${SYSTEMD_REQUIRES:-docker.service}"
  SYSTEMD_WANTS="${SYSTEMD_WANTS:-network-online.target}"
  resolve_run_user "${SUDO_USER:-root}"
  resolve_docker_compose_cmd
}

resolve_docker_compose_cmd() {
  local docker_bin
  docker_bin="$(command -v docker 2>/dev/null || true)"
  if [[ -z "${docker_bin}" ]]; then
    return 0
  fi
  DOCKER_COMPOSE_CMD="${docker_bin} compose"
  export DOCKER_COMPOSE_CMD
}

# Ajusta NILOCARDMED_RUN_USER/GROUP si el valor de deploy.env no existe en el sistema.
resolve_run_user() {
  local fallback="${1:-${SUDO_USER:-root}}"
  local user="${NILOCARDMED_RUN_USER:-${fallback}}"
  local group="${NILOCARDMED_RUN_GROUP:-${user}}"

  if [[ "${fallback}" == "root" || -z "${fallback}" ]]; then
    fallback="$(getent passwd 1000 | cut -d: -f1 || true)"
    fallback="${fallback:-root}"
  fi

  if ! id "${user}" >/dev/null 2>&1; then
    if id "${fallback}" >/dev/null 2>&1; then
      log_info "Usuario '${user}' no existe; usando '${fallback}'"
      user="${fallback}"
    else
      log_warn "Usuario '${user}' no existe; usando 'root' para systemd"
      user="root"
    fi
  fi

  if ! getent group "${group}" >/dev/null 2>&1; then
    group="${user}"
  fi

  export NILOCARDMED_RUN_USER="${user}"
  export NILOCARDMED_RUN_GROUP="${group}"
}

render_template() {
  local template_path="$1"
  local output_path="$2"
  local content
  content="$(cat "${template_path}")"
  content="${content//@INSTALL_DIR@/${INSTALL_DIR}}"
  content="${content//@NILOCARDMED_RUN_USER@/${NILOCARDMED_RUN_USER}}"
  content="${content//@NILOCARDMED_RUN_GROUP@/${NILOCARDMED_RUN_GROUP}}"
  content="${content//@DOCKER_COMPOSE_CMD@/${DOCKER_COMPOSE_CMD}}"
  content="${content//@COMPOSE_FILE@/${COMPOSE_FILE}}"
  content="${content//@COMPOSE_PROJECT_NAME@/${COMPOSE_PROJECT_NAME}}"
  content="${content//@SYSTEMD_WANTED_BY@/${SYSTEMD_WANTED_BY}}"
  content="${content//@SYSTEMD_AFTER@/${SYSTEMD_AFTER}}"
  content="${content//@SYSTEMD_REQUIRES@/${SYSTEMD_REQUIRES}}"
  content="${content//@SYSTEMD_WANTS@/${SYSTEMD_WANTS}}"
  printf '%s\n' "${content}" > "${output_path}"
}

ensure_host_directories() {
  local data_dir="${HOST_DATA_DIR:-/var/lib/nilocardmed/data}"
  local logs_dir="${HOST_LOG_DIR:-/var/lib/nilocardmed/logs}"

  if [[ "${data_dir}" != /* ]]; then
    log_info "HOST_DATA_DIR es volumen nombrado de Docker: ${data_dir}"
    return
  fi

  log_info "Creando directorios en host: ${data_dir}, ${logs_dir}"
  mkdir -p "${data_dir}" "${logs_dir}"
  resolve_run_user "${SUDO_USER:-root}"
  chown -R "${NILOCARDMED_RUN_USER}:${NILOCARDMED_RUN_GROUP}" "${data_dir}" "${logs_dir}"
}

compose_cmd() {
  local -a cmd
  cmd=( ${DOCKER_COMPOSE_CMD} )
  local IFS=':'
  read -ra files <<< "${COMPOSE_FILE}"
  for f in "${files[@]}"; do
    cmd+=( -f "${INSTALL_DIR}/${f}" )
  done
  if [[ -f "${INSTALL_DIR}/docker-compose.override.yml" ]] \
    && [[ "${COMPOSE_FILE}" != *docker-compose.override.yml* ]]; then
    cmd+=( -f "${INSTALL_DIR}/docker-compose.override.yml" )
  fi
  "${cmd[@]}" "$@"
}

run_compose_in_install_dir() {
  (
    cd "${INSTALL_DIR}"
    export COMPOSE_PROJECT_NAME COMPOSE_FILE
    compose_cmd "$@"
  )
}

# BlueZ en el host: Experimental, discoverable, alias BLE (requiere root).
ensure_bluetooth_host_ready() {
  local install_dir="${1:-${INSTALL_DIR:-}}"

  if [[ -z "${install_dir}" ]]; then
    log_warn "ensure_bluetooth_host_ready: INSTALL_DIR no definido"
    return 0
  fi

  if ! is_true "${ENABLE_BLUETOOTH:-false}"; then
    log_info "ENABLE_BLUETOOTH=false — omitiendo ensure-bluetooth-powered"
    return 0
  fi

  local script="${install_dir}/scripts/ensure-bluetooth-powered.sh"
  if [[ ! -f "${script}" ]]; then
    log_warn "No encontrado: ${script}"
    return 0
  fi

  log_info "=== Bluetooth host (BlueZ Experimental, discoverable, alias) ==="
  INSTALL_DIR="${install_dir}" ENV_FILE="${install_dir}/.env" \
    bash "${script}" || log_warn "ensure-bluetooth-powered falló — revisa: bluetoothctl show"
}
