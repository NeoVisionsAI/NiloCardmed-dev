#!/usr/bin/env bash
# Instala NiloCardmed en Raspberry Pi (Docker + systemd).
# Uso:
#   sudo ./scripts/install.sh
#   sudo INSTALL_DIR=/opt/nilocardmed ./scripts/install.sh
#   sudo DEPLOY_ENV_FILE=/ruta/deploy.env ./scripts/install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

if [[ "${EUID}" -ne 0 ]]; then
  log_error "Ejecuta este script como root (sudo)."
  exit 1
fi

export INSTALL_DIR="${INSTALL_DIR:-${REPO_ROOT}}"
export DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-${INSTALL_DIR}/deploy.env}"

load_deploy_env

# En instalación de producción, usar NILOCARDMED_INSTALL_DIR si no se pasó INSTALL_DIR.
if [[ "${INSTALL_DIR}" == "${REPO_ROOT}" && -n "${NILOCARDMED_INSTALL_DIR:-}" ]]; then
  INSTALL_DIR="${NILOCARDMED_INSTALL_DIR}"
  export INSTALL_DIR
fi

log_info "Instalando NiloCardmed en ${INSTALL_DIR}"

require_command docker

if ! docker compose version >/dev/null 2>&1; then
  log_error "Docker Compose v2 no disponible. Ajusta DOCKER_COMPOSE_CMD en deploy.env"
  exit 1
fi

if [[ ! -f "${DEPLOY_ENV_FILE}" ]]; then
  if [[ -f "${REPO_ROOT}/deploy.env.example" ]]; then
    log_info "Creando ${DEPLOY_ENV_FILE} desde deploy.env.example"
    cp "${REPO_ROOT}/deploy.env.example" "${DEPLOY_ENV_FILE}"
  else
    log_error "No se encontró deploy.env ni deploy.env.example"
    exit 1
  fi
  load_deploy_env
fi

if [[ "${INSTALL_DIR}" != "${REPO_ROOT}" ]]; then
  log_info "Copiando proyecto a ${INSTALL_DIR}"
  mkdir -p "${INSTALL_DIR}"
  rsync -a --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude 'data' \
    --exclude '__pycache__' \
    "${REPO_ROOT}/" "${INSTALL_DIR}/"
fi

if [[ ! -f "${INSTALL_DIR}/.env" && -f "${INSTALL_DIR}/.env.example" ]]; then
  log_info "Creando ${INSTALL_DIR}/.env desde .env.example"
  cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
fi

ensure_host_directories

INSTALL_DIR="${INSTALL_DIR}" DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE}" \
  bash "${INSTALL_DIR}/scripts/generate-compose-override.sh"

log_info "Construyendo imagen Docker (plataforma: ${DOCKER_DEFAULT_PLATFORM:-linux/arm/v7})"
(
  cd "${INSTALL_DIR}"
  export COMPOSE_PROJECT_NAME COMPOSE_FILE
  # shellcheck disable=SC1090
  set -a && source "${DEPLOY_ENV_FILE}" && set +a
  ${DOCKER_COMPOSE_CMD} build \
    --build-arg PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.11-slim-bookworm}" \
    --build-arg APP_USER="${APP_USER:-nilocardmed}" \
    --build-arg APP_UID="${APP_UID:-1000}" \
    --build-arg APP_GID="${APP_GID:-1000}" \
    --build-arg APP_HOME="${APP_HOME:-/app}" \
    --build-arg DATA_DIR="${CONTAINER_DATA_DIR:-/data}" \
    --build-arg LOG_DIR="${CONTAINER_LOG_DIR:-/var/log/nilocardmed}"
)

service_path="/etc/systemd/system/${NILOCARDMED_SERVICE_NAME}.service"
log_info "Instalando unidad systemd: ${service_path}"
render_template \
  "${INSTALL_DIR}/deploy/systemd/nilocardmed.service.in" \
  "${service_path}"

systemctl daemon-reload
systemctl enable "${NILOCARDMED_SERVICE_NAME}.service"
systemctl restart "${NILOCARDMED_SERVICE_NAME}.service"

log_info "Estado del servicio:"
systemctl --no-pager status "${NILOCARDMED_SERVICE_NAME}.service" || true

log_info "Instalación completada."
log_info "Logs: journalctl -u ${NILOCARDMED_SERVICE_NAME} -f"
log_info "Docker: cd ${INSTALL_DIR} && ${DOCKER_COMPOSE_CMD} ps"
