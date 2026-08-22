#!/usr/bin/env bash
# Instala NiloCardmed en Raspberry Pi (dependencias + Docker + systemd).
#
# Uso típico en fábrica (desde clone en ~/dev o /opt):
#   sudo ./scripts/install.sh
#
# Instala siempre en NILOCARDMED_INSTALL_DIR (/opt/nilocardmed por defecto),
# configura systemd, Docker, BLE, WiFi y arranca el servicio.
#
# Opciones:
#   --skip-host-deps   Docker/apt ya instalados
#   INSTALL_DIR=/ruta    Forzar directorio (dev); por defecto /opt/nilocardmed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export REPO_ROOT

# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

SKIP_HOST_DEPS=false
SKIP_BUILD=false
for arg in "$@"; do
  case "${arg}" in
    --skip-host-deps) SKIP_HOST_DEPS=true ;;
    --skip-build) SKIP_BUILD=true ;;
    -h | --help)
      cat <<'EOF'
Uso: sudo ./scripts/install.sh [opciones]

Instalación completa para fábrica (repetible en N dispositivos):
  1. Paquetes apt + Docker + servicios (BLE, WiFi, D-Bus)
  2. Grupos del usuario (docker, video, bluetooth…)
  3. Copia del proyecto a /opt/nilocardmed (por defecto)
  4. deploy.env + .env (uuid, contraseña BLE, flags producción)
  5. Build Docker + systemd + arranque verificado

Opciones:
  --skip-host-deps   Omitir apt/Docker (solo aplicación)
  --skip-build       No reconstruir imagen (usa la existente; ideal tras cambios de código)
  -h, --help         Esta ayuda

Variables:
  INSTALL_DIR              Directorio destino (default: NILOCARDMED_INSTALL_DIR)
  SKIP_HOST_DEPS=true      Igual que --skip-host-deps
  SKIP_BUILD=true          Igual que --skip-build

Iteración rápida en Pi:
  sudo ./scripts/update.sh              # ~segundos (código + Bluetooth host + restart)
  sudo ./scripts/update.sh --build      # rebuild con caché Docker (~1-3 min si solo cambió código)

Incluye automáticamente scripts/ensure-bluetooth-powered.sh (BlueZ, discoverable, alias).
EOF
      exit 0
      ;;
  esac
done
export SKIP_HOST_DEPS
export SKIP_BUILD

if [[ "${EUID}" -ne 0 ]]; then
  log_error "Ejecuta este script como root (sudo)."
  exit 1
fi

# --- 1. Resolver rutas y usuario ---
INSTALL_DIR="$(resolve_install_dir_from_repo "${REPO_ROOT}")"
export INSTALL_DIR
export DEPLOY_ENV_FILE="${INSTALL_DIR}/deploy.env"

INVOKING_USER="${SUDO_USER:-$(id -un)}"
if [[ "${INVOKING_USER}" == "root" ]]; then
  INVOKING_USER="$(getent passwd 1000 | cut -d: -f1 || echo root)"
fi
export NILOCARDMED_RUN_USER="${INVOKING_USER}"
resolve_run_user "${INVOKING_USER}"

log_info "=== NiloCardmed — instalación ==="
log_info "Origen:  ${REPO_ROOT}"
log_info "Destino: ${INSTALL_DIR}"
log_info "Usuario: ${NILOCARDMED_RUN_USER} (grupo ${NILOCARDMED_RUN_GROUP})"

# --- 2. Host (apt, Docker, grupos) ---
# shellcheck source=lib/install-host-deps.sh
source "${SCRIPT_DIR}/lib/install-host-deps.sh"
install_host_dependencies "${NILOCARDMED_RUN_USER}"

# --- 3. Copiar proyecto al destino de producción ---
sync_project_to_install_dir "${REPO_ROOT}" "${INSTALL_DIR}"
ensure_bluezero_dbus_policy

# --- 4. Configuración (.env, deploy.env, uuid, contraseña) ---
# shellcheck source=lib/setup-env.sh
source "${INSTALL_DIR}/scripts/lib/setup-env.sh"
setup_deploy_and_app_env "${INSTALL_DIR}"

# --- 5. Cargar deploy.env del destino ---
export INSTALL_DIR
export DEPLOY_ENV_FILE="${INSTALL_DIR}/deploy.env"
load_deploy_env
ensure_run_user_groups "${NILOCARDMED_RUN_USER}"
ensure_host_directories

# --- 6. Compose override (hot-plug cámara, BLE, WiFi) ---
INSTALL_DIR="${INSTALL_DIR}" DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE}" \
  bash "${INSTALL_DIR}/scripts/generate-compose-override.sh"
load_deploy_env
ensure_install_dir_permissions "${INSTALL_DIR}"

# --- 7. Build imagen (opcional; capas cacheadas entre builds) ---
if is_true "${SKIP_BUILD:-false}"; then
  log_info "=== Build Docker omitido (--skip-build) ==="
  if ! docker image inspect "${COMPOSE_PROJECT_NAME:-nilocardmed}_nilocardmed:latest" >/dev/null 2>&1 \
    && ! docker image inspect nilocardmed:latest >/dev/null 2>&1 \
    && ! docker images --format '{{.Repository}}' | grep -qx nilocardmed; then
    log_warn "No hay imagen nilocardmed local; el arranque puede fallar hasta un build."
    log_warn "Ejecuta: sudo ./scripts/install.sh --skip-host-deps  (o update.sh --build)"
  fi
else
  log_info "=== Build Docker (plataforma: ${DOCKER_DEFAULT_PLATFORM:-linux/arm/v7}) ==="
  log_info "Primera build ~15-30 min; cambios solo de código ~1-3 min (caché de capas pip)."
  export DOCKER_BUILDKIT=1
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
fi

# --- 8. systemd ---
service_path="/etc/systemd/system/${NILOCARDMED_SERVICE_NAME}.service"
log_info "=== systemd: ${service_path} ==="
render_template \
  "${INSTALL_DIR}/deploy/systemd/nilocardmed.service.in" \
  "${service_path}"

ensure_run_user_groups "${NILOCARDMED_RUN_USER}"

systemctl daemon-reload
systemctl enable "${NILOCARDMED_SERVICE_NAME}.service"

# Parar contenedor previo si existía (compose project correcto)
run_compose_in_install_dir down --remove-orphans 2>/dev/null || true

ensure_bluetooth_host_ready "${INSTALL_DIR}"

log_info "=== Reinicio del servicio ${NILOCARDMED_SERVICE_NAME} ==="
systemctl restart "${NILOCARDMED_SERVICE_NAME}.service"

if ! verify_service_and_container; then
  log_error "Instalación incompleta — revisa los logs arriba"
  exit 1
fi

log_info ""
log_info "=== Instalación completada ==="
log_info "Directorio:  ${INSTALL_DIR}"
log_info "Device ID:   $(read_env_value "${INSTALL_DIR}/.env" NILOCARDMED_SER__DEVICE_ID || echo '?')"
log_info "Nombre BLE:  $(read_env_value "${INSTALL_DIR}/.env" NILOCARDMED_BLUETOOTH__DEVICE_NAME || echo '?')"
log_info "Identidad:   ${HOST_DATA_DIR:-/var/lib/nilocardmed/data}/device-identity.env"
log_info ""
log_info "Comandos útiles:"
log_info "  sudo systemctl status ${NILOCARDMED_SERVICE_NAME}"
log_info "  sudo bash ${INSTALL_DIR}/scripts/pi-start.sh status"
log_info "  sudo bash ${INSTALL_DIR}/scripts/pi-start.sh trace"
log_info "  journalctl -u ${NILOCARDMED_SERVICE_NAME} -f"
