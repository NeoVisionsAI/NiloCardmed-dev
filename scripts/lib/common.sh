#!/usr/bin/env bash

set -euo pipefail

log_info() {
  echo "[nilocardmed] $*"
}

log_error() {
  echo "[nilocardmed][ERROR] $*" >&2
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
  fi
  NILOCARDMED_SERVICE_NAME="${NILOCARDMED_SERVICE_NAME:-nilocardmed}"
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
  chown -R "${NILOCARDMED_RUN_USER}:${NILOCARDMED_RUN_GROUP}" "${data_dir}" "${logs_dir}"
}
