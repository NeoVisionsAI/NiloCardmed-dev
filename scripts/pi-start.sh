#!/usr/bin/env bash
# Comprueba la Raspberry Pi y arranca NiloCardmed (Docker).
#
# Ejecutar EN LA PI, desde el directorio del proyecto:
#   chmod +x scripts/pi-start.sh
#   ./scripts/pi-start.sh check          # solo comprobaciones
#   ./scripts/pi-start.sh start          # comprobar + construir + arrancar
#   ./scripts/pi-start.sh start --build  # fuerza rebuild de imagen
#   ./scripts/pi-start.sh install        # instalación completa (systemd + build)
#
# Desde tu PC (copiar proyecto y ejecutar):
#   rsync -av --exclude .git --exclude .venv --exclude data/ \
#     ./ pi@<IP-DE-TU-PI>:/opt/nilocardmed/
#   ssh pi@<IP-DE-TU-PI> 'cd /opt/nilocardmed && ./scripts/pi-start.sh start'

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

CHECK_ONLY=false
DO_BUILD=false
DO_INSTALL=false
FORCE_BUILD=false

usage() {
  cat <<'EOF'
Uso: pi-start.sh <comando> [opciones]

Comandos:
  check     Comprobaciones previas (Docker, cámara, BLE, WiFi…)
  start     check + generar override + docker compose up -d
  install   check + install.sh → /opt/nilocardmed (instalación fábrica)
  status    Estado del contenedor y salud
  stop      docker compose down (usa deploy.env, proyecto nilocardmed)
  logs      docker compose logs -f --tail=100
  trace     Traza operativa BLE/WiFi (filtrada). Ver también: trace-full
  trace-full  Todos los logs del contenedor (sin filtro grep)
  ble-recover  Recupera BLE tras emparejamiento huérfano / sin anuncio (requiere sudo)
  deploy    Alias de update.sh: código + Bluetooth host + rebuild opcional + restart

Opciones (start / install / deploy):
  --build   Reconstruir imagen Docker antes de arrancar

Ejemplos:
  ./scripts/pi-start.sh check
  ./scripts/pi-start.sh start
  ./scripts/pi-start.sh start --build
  ./scripts/pi-start.sh install
  sudo ./scripts/pi-start.sh deploy          # actualización rápida (sin rebuild)
  sudo ./scripts/pi-start.sh deploy --build  # rebuild + Bluetooth + restart systemd
EOF
}

parse_args() {
  local cmd="${1:-start}"
  shift || true
  case "${cmd}" in
    check)
      CHECK_ONLY=true
      ;;
    start)
      DO_BUILD=true
      ;;
    install)
      DO_INSTALL=true
      DO_BUILD=true
      ;;
    status)
      cmd_status
      exit 0
      ;;
    stop)
      cmd_stop
      exit 0
      ;;
    logs)
      cmd_logs
      exit 0
      ;;
    trace)
      cmd_trace "$@"
      exit 0
      ;;
    trace-full | tracefull)
      cmd_trace_full
      exit 0
      ;;
    ble-recover | bluetooth-recover)
      cmd_ble_recover "$@"
      exit 0
      ;;
    deploy | update)
      cmd_deploy "$@"
      exit 0
      ;;
    -h | --help | help)
      usage
      exit 0
      ;;
    *)
      log_error "Comando desconocido: ${cmd}"
      usage
      exit 2
      ;;
  esac
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --build) FORCE_BUILD=true ;;
      -h | --help) usage; exit 0 ;;
      *) log_error "Opción desconocida: $1"; exit 2 ;;
    esac
    shift
  done
}

FAILURES=0
WARNINGS=0

check_ok() {
  log_info "[OK] $*"
}

check_warn() {
  log_info "[AVISO] $*"
  WARNINGS=$((WARNINGS + 1))
}

check_fail() {
  log_error "[FALLO] $*"
  FAILURES=$((FAILURES + 1))
}

run_checks() {
  log_info "=== Comprobaciones NiloCardmed en $(hostname) ==="
  log_info "Directorio: ${INSTALL_DIR}"

  local arch
  arch="$(uname -m)"
  case "${arch}" in
    armv7l | armv6l | aarch64) check_ok "Arquitectura ARM: ${arch}" ;;
    x86_64 | amd64) check_warn "Arquitectura ${arch} (PC de desarrollo, no Pi)" ;;
    *) check_warn "Arquitectura no habitual: ${arch}" ;;
  esac

  if command -v docker >/dev/null 2>&1; then
    check_ok "Docker: $(docker --version | head -1)"
  else
    check_fail "Docker no instalado"
  fi

  if docker info >/dev/null 2>&1; then
    check_ok "Docker daemon activo"
  else
    check_fail "Docker daemon no responde (¿sudo usermod -aG docker ${USER}? ¿servicio activo?)"
  fi

  if docker compose version >/dev/null 2>&1; then
    check_ok "Docker Compose: $(docker compose version --short 2>/dev/null || docker compose version)"
  else
    check_fail "Docker Compose v2 no disponible"
  fi

  if [[ -f "${INSTALL_DIR}/deploy.env" ]]; then
    check_ok "deploy.env presente"
  else
    check_fail "Falta deploy.env (cp deploy.env.example deploy.env)"
  fi

  if [[ -f "${INSTALL_DIR}/.env" ]]; then
    check_ok ".env presente"
    if grep -qE '^NILOCARDMED_BLUETOOTH__PASSWORD=changeme' "${INSTALL_DIR}/.env" 2>/dev/null; then
      check_warn "Contraseña BLE sigue siendo 'changeme' — cámbiala antes de producción"
    fi
    if ! grep -qE '^NILOCARDMED_SER__URL=.+' "${INSTALL_DIR}/.env" 2>/dev/null; then
      check_warn "NILOCARDMED_SER__URL no configurada en .env"
    fi
  else
    check_fail "Falta .env (cp .env.example .env y edita contraseña BLE + URL SER)"
  fi

  if [[ -e "${VIDEO_DEVICE_HOST:-/dev/video0}" ]]; then
    check_ok "Cámara USB: ${VIDEO_DEVICE_HOST:-/dev/video0}"
  else
    check_warn "No hay cámara USB ahora — normal si está desconectada (hot-plug; conecta cuando quieras)"
  fi

  if [[ -S /var/run/dbus/system_bus_socket ]]; then
    check_ok "D-Bus system (necesario para BLE/WiFi en contenedor)"
  else
    check_fail "No hay /var/run/dbus/system_bus_socket — BLE/WiFi en contenedor fallarán"
  fi

  if is_true "${ENABLE_BLUETOOTH:-false}"; then
    check_ok "ENABLE_BLUETOOTH=true en deploy.env"
    if systemctl is-active --quiet bluetooth 2>/dev/null || pgrep -x bluetoothd >/dev/null 2>&1; then
      check_ok "Servicio bluetooth del host activo"
    else
      check_warn "Servicio bluetooth del host no detectado — prueba: sudo systemctl enable --now bluetooth"
    fi
  else
    check_fail "ENABLE_BLUETOOTH=false — pon ENABLE_BLUETOOTH=true en deploy.env (configuración por tablet)"
  fi

  if is_true "${ENABLE_WIFI:-false}"; then
    check_ok "ENABLE_WIFI=true en deploy.env"
    if command -v nmcli >/dev/null 2>&1; then
      check_ok "nmcli disponible (WiFi vía host)"
    else
      check_warn "nmcli no encontrado — instala NetworkManager para WiFi desde la app"
    fi
  else
    check_fail "ENABLE_WIFI=false — pon ENABLE_WIFI=true en deploy.env (WiFi se configura luego por BLE)"
  fi

  local data_dir="${HOST_DATA_DIR:-/var/lib/nilocardmed/data}"
  local logs_dir="${HOST_LOG_DIR:-/var/lib/nilocardmed/logs}"
  if [[ "${data_dir}" == /* ]]; then
    if [[ -d "${data_dir}" ]] || mkdir -p "${data_dir}" 2>/dev/null; then
      check_ok "Datos: ${data_dir}"
    else
      check_warn "No se pudo crear ${data_dir} — ejecuta con sudo o crea manualmente"
    fi
  fi
  if [[ "${logs_dir}" == /* ]]; then
    if [[ -d "${logs_dir}" ]] || mkdir -p "${logs_dir}" 2>/dev/null; then
      check_ok "Logs: ${logs_dir}"
    else
      check_warn "No se pudo crear ${logs_dir}"
    fi
  fi

  log_info "=== Resumen: ${FAILURES} fallo(s), ${WARNINGS} aviso(s) ==="
  if [[ "${FAILURES}" -gt 0 ]]; then
    return 1
  fi
  return 0
}

ensure_deploy_flags() {
  local deploy_file="${INSTALL_DIR}/deploy.env"
  local changed=false

  for key_val in \
    "ENABLE_BLUETOOTH=true" \
    "ENABLE_WIFI=true" \
    "MOUNT_USB_BUS=true" \
    "DOCKER_DEFAULT_PLATFORM=linux/arm/v7"; do
    local key="${key_val%%=*}"
    local val="${key_val#*=}"
    if [[ -f "${deploy_file}" ]] && grep -q "^${key}=" "${deploy_file}"; then
      if ! grep -q "^${key}=${val}$" "${deploy_file}"; then
        log_info "Ajustando ${key}=${val} en deploy.env"
        sed -i "s|^${key}=.*|${key}=${val}|" "${deploy_file}"
        changed=true
      fi
    fi
  done

  if [[ "${changed}" == true ]]; then
    load_deploy_env
  fi
}

compose_cmd() {
  run_compose_in_install_dir "$@"
}

prepare_compose() {
  ensure_deploy_flags
  if [[ ! -f "${INSTALL_DIR}/.env" && -f "${INSTALL_DIR}/.env.example" ]]; then
    log_info "Creando .env desde .env.example — EDITA contraseña BLE y URL SER"
    cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
  fi
  if [[ ! -f "${INSTALL_DIR}/deploy.env" && -f "${INSTALL_DIR}/deploy.env.example" ]]; then
    cp "${INSTALL_DIR}/deploy.env.example" "${INSTALL_DIR}/deploy.env"
    load_deploy_env
    ensure_deploy_flags
  fi

  INSTALL_DIR="${INSTALL_DIR}" DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE}" \
    bash "${INSTALL_DIR}/scripts/generate-compose-override.sh"

  ensure_host_directories 2>/dev/null || true
}

start_stack() {
  prepare_compose
  cd "${INSTALL_DIR}"
  export COMPOSE_PROJECT_NAME COMPOSE_FILE

  if [[ "${FORCE_BUILD}" == true ]] || [[ "${DO_BUILD}" == true ]]; then
    log_info "Construyendo imagen (puede tardar 10-20 min en Pi Zero)..."
    compose_cmd build
  fi

  log_info "Arrancando contenedor..."
  ensure_bluetooth_host_ready "${INSTALL_DIR}"
  compose_cmd up -d --remove-orphans

  sleep 3
  compose_cmd ps

  log_info "Esperando healthcheck inicial..."
  local i
  for i in $(seq 1 24); do
    if compose_cmd exec -T "${COMPOSE_SERVICE_NAME}" \
      python -m nilocardmed.main health check --exit-code >/dev/null 2>&1; then
      check_ok "Contenedor healthy"
      break
    fi
    if compose_cmd exec -T "${COMPOSE_SERVICE_NAME}" \
      python -m nilocardmed.main health status 2>/dev/null | grep -q '"status": "degraded"'; then
      check_ok "Contenedor en marcha (degraded — normal sin WiFi; configura por tablet)"
      break
    fi
    if [[ "${i}" -eq 24 ]]; then
      check_warn "Healthcheck aún no OK — revisa: ./scripts/pi-start.sh logs"
    fi
    sleep 5
  done

  log_info ""
  log_info "=== NiloCardmed arrancado ==="
  log_info "BLE: escanea '${NILOCARDMED_DEVICE_NAME:-NiloCardmed}' desde la tablet"
  log_info "Contraseña BLE: la de NILOCARDMED_BLUETOOTH__PASSWORD en .env"
  log_info "Logs: ./scripts/pi-start.sh logs"
  log_info "Estado: ./scripts/pi-start.sh status"
}

cmd_status() {
  load_deploy_env
  cd "${INSTALL_DIR}"
  export COMPOSE_PROJECT_NAME COMPOSE_FILE
  compose_cmd ps 2>/dev/null || docker ps --filter name=nilocardmed
  echo "---"
  compose_cmd exec -T "${COMPOSE_SERVICE_NAME}" \
    python -m nilocardmed.main health status 2>/dev/null || true
}

cmd_stop() {
  load_deploy_env
  cd "${INSTALL_DIR}"
  export COMPOSE_PROJECT_NAME COMPOSE_FILE
  log_info "Parando proyecto Docker '${COMPOSE_PROJECT_NAME}' en ${INSTALL_DIR}"
  compose_cmd down --remove-orphans
}

cmd_logs() {
  load_deploy_env
  cd "${INSTALL_DIR}"
  export COMPOSE_PROJECT_NAME COMPOSE_FILE
  compose_cmd logs -f --tail=100
}

cmd_trace() {
  local mode="${1:-}"
  load_deploy_env
  cd "${INSTALL_DIR}"
  export COMPOSE_PROJECT_NAME COMPOSE_FILE
  log_info "Traza operativa (Ctrl+C para salir)"
  log_info "Si no ves wifi_scan: el comando no llegó al contenedor (timeout BLE / app)"
  if [[ "${mode}" == "--full" ]]; then
    cmd_trace_full
    return
  fi
  compose_cmd logs -f --tail=80 2>&1 | grep --line-buffered -iE \
    'nilocardmed\.trace|comando BLE|cliente BLE|wifi_scan|wifi_connect|wifi_scan_ok|wifi_scan_error|Escaneando redes|\[ble\]|\[wifi\]|\[config\]|\[system\]|invalid_password|wifi_error|bluetooth_activo|autenticación|auth '
}

cmd_trace_full() {
  load_deploy_env
  cd "${INSTALL_DIR}"
  export COMPOSE_PROJECT_NAME COMPOSE_FILE
  log_info "Logs completos del contenedor (Ctrl+C para salir)"
  compose_cmd logs -f --tail=100
}

cmd_deploy() {
  if [[ "${EUID}" -ne 0 ]]; then
    log_error "deploy requiere root: sudo ./scripts/pi-start.sh deploy [--build]"
    exit 1
  fi

  local update_script="${REPO_ROOT}/scripts/update.sh"
  if [[ ! -f "${update_script}" ]]; then
    update_script="${INSTALL_DIR}/scripts/update.sh"
  fi
  if [[ ! -f "${update_script}" ]]; then
    log_error "No se encontró scripts/update.sh"
    exit 1
  fi

  log_info "Despliegue: ${update_script} $*"
  log_info "(incluye ensure-bluetooth-powered + systemctl restart nilocardmed)"
  exec bash "${update_script}" "$@"
}

cmd_ble_recover() {
  if [[ "${EUID}" -ne 0 ]]; then
    log_error "ble-recover requiere root: sudo ./scripts/pi-start.sh ble-recover [--purge-bonds]"
    exit 1
  fi
  local script="${REPO_ROOT}/scripts/bluetooth-recover.sh"
  if [[ ! -f "${script}" ]]; then
    script="${INSTALL_DIR}/scripts/bluetooth-recover.sh"
  fi
  if [[ ! -f "${script}" ]]; then
    log_error "No se encontró scripts/bluetooth-recover.sh"
    exit 1
  fi
  exec bash "${script}" "$@"
}

main() {
  parse_args "$@"
  export INSTALL_DIR="${INSTALL_DIR:-$(resolve_install_dir_from_repo "${REPO_ROOT}")}"
  export DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-${INSTALL_DIR}/deploy.env}"
  load_deploy_env

  if ! run_checks; then
    if [[ "${CHECK_ONLY}" == true ]]; then
      exit 1
    fi
    log_error "Corrige los fallos antes de arrancar."
    exit 1
  fi

  if [[ "${CHECK_ONLY}" == true ]]; then
    log_info "Comprobaciones OK. Ejecuta: ./scripts/pi-start.sh start"
    exit 0
  fi

  if [[ "${DO_INSTALL}" == true ]]; then
    if [[ "${EUID}" -ne 0 ]]; then
      log_error "install requiere root: sudo ./scripts/pi-start.sh install"
      exit 1
    fi
    exec bash "${REPO_ROOT}/scripts/install.sh"
  fi

  start_stack
}

main "$@"
