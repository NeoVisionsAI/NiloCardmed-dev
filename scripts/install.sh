#!/usr/bin/env bash
# Instala NiloCardmed en Raspberry Pi (dependencias + Docker + systemd).
# Uso:
#   sudo ./scripts/install.sh
#   sudo INSTALL_DIR=/opt/nilocardmed ./scripts/install.sh
#   sudo ./scripts/install.sh --skip-host-deps   # solo app (Docker ya instalado)
#   sudo DEPLOY_ENV_FILE=/ruta/deploy.env ./scripts/install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

SKIP_HOST_DEPS=false
for arg in "$@"; do
  case "${arg}" in
    --skip-host-deps) SKIP_HOST_DEPS=true ;;
    -h | --help)
      cat <<'EOF'
Uso: sudo ./scripts/install.sh [opciones]

Instala dependencias del host (apt, Docker, BLE, WiFi), configura .env/deploy.env,
construye la imagen Docker e instala el servicio systemd.

Opciones:
  --skip-host-deps   No instala paquetes ni Docker (solo NiloCardmed)
  -h, --help         Esta ayuda

Variables de entorno:
  INSTALL_DIR        Directorio de instalación (default: repo actual o NILOCARDMED_INSTALL_DIR)
  SKIP_HOST_DEPS     true = equivalente a --skip-host-deps
EOF
      exit 0
      ;;
  esac
done
export SKIP_HOST_DEPS

if [[ "${EUID}" -ne 0 ]]; then
  log_error "Ejecuta este script como root (sudo)."
  exit 1
fi

export INSTALL_DIR="${INSTALL_DIR:-${REPO_ROOT}}"
export DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-${INSTALL_DIR}/deploy.env}"

# Usuario de ejecución (deploy.env.example → deploy.env → sudo user)
RUN_USER="${SUDO_USER:-$(id -un)}"
if [[ -f "${REPO_ROOT}/deploy.env" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/deploy.env" 2>/dev/null || true
  RUN_USER="${NILOCARDMED_RUN_USER:-${RUN_USER}}"
elif [[ -f "${REPO_ROOT}/deploy.env.example" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/deploy.env.example" 2>/dev/null || true
  RUN_USER="${NILOCARDMED_RUN_USER:-${RUN_USER}}"
fi

log_info "Instalando NiloCardmed en ${INSTALL_DIR} (usuario: ${RUN_USER})"

# shellcheck source=lib/install-host-deps.sh
source "${SCRIPT_DIR}/lib/install-host-deps.sh"
install_host_dependencies "${RUN_USER}"

# En instalación de producción, usar NILOCARDMED_INSTALL_DIR si existe en deploy.env.example
if [[ -f "${REPO_ROOT}/deploy.env.example" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/deploy.env.example" 2>/dev/null || true
fi
if [[ "${INSTALL_DIR}" == "${REPO_ROOT}" && -n "${NILOCARDMED_INSTALL_DIR:-}" ]]; then
  INSTALL_DIR="${NILOCARDMED_INSTALL_DIR}"
  export INSTALL_DIR
  DEPLOY_ENV_FILE="${INSTALL_DIR}/deploy.env"
  export DEPLOY_ENV_FILE
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

# deploy.env + .env desde plantillas; UUID BLE persistente; contraseña interactiva
# shellcheck source=lib/setup-env.sh
source "${INSTALL_DIR}/scripts/lib/setup-env.sh"
setup_deploy_and_app_env "${INSTALL_DIR}"

load_deploy_env
ensure_host_directories

INSTALL_DIR="${INSTALL_DIR}" DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE}" \
  bash "${INSTALL_DIR}/scripts/generate-compose-override.sh"

log_info "Construyendo imagen Docker (plataforma: ${DOCKER_DEFAULT_PLATFORM:-linux/arm/v7})"
log_info "En Pi Zero la primera build puede tardar 15-30 minutos..."
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
log_info "Device ID (SER): $(read_env_value "${INSTALL_DIR}/.env" NILOCARDMED_SER__DEVICE_ID || echo '?')"
log_info "Nombre BLE: $(read_env_value "${INSTALL_DIR}/.env" NILOCARDMED_BLUETOOTH__DEVICE_NAME || echo '?')"
log_info "Identidad: ${HOST_DATA_DIR:-/var/lib/nilocardmed/data}/device-identity.env"
log_info "Logs: journalctl -u ${NILOCARDMED_SERVICE_NAME} -f"
log_info "Docker: cd ${INSTALL_DIR} && ${DOCKER_COMPOSE_CMD} ps"
