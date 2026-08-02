#!/usr/bin/env bash
# Desinstala el servicio systemd y detiene contenedores (no borra volúmenes/datos).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

if [[ "${EUID}" -ne 0 ]]; then
  log_error "Ejecuta este script como root (sudo)."
  exit 1
fi

load_deploy_env
require_command docker

service_path="/etc/systemd/system/${NILOCARDMED_SERVICE_NAME}.service"

if [[ -f "${service_path}" ]]; then
  systemctl stop "${NILOCARDMED_SERVICE_NAME}.service" || true
  systemctl disable "${NILOCARDMED_SERVICE_NAME}.service" || true
  rm -f "${service_path}"
  systemctl daemon-reload
  log_info "Servicio ${NILOCARDMED_SERVICE_NAME} eliminado"
fi

if [[ -d "${INSTALL_DIR}" ]]; then
  (
    cd "${INSTALL_DIR}"
    export COMPOSE_PROJECT_NAME COMPOSE_FILE
    if [[ -f "${INSTALL_DIR}/deploy.env" ]]; then
      set -a && source "${INSTALL_DIR}/deploy.env" && set +a
    fi
    ${DOCKER_COMPOSE_CMD} down --remove-orphans || true
  )
fi

log_info "Desinstalación completada. Los datos en HOST_DATA_DIR no se han eliminado."
