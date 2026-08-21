#!/usr/bin/env bash
# Instala dependencias del host: apt, Docker, BLE, WiFi, utilidades.

set -euo pipefail

# shellcheck source=common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

HOST_APT_PACKAGES=(
  ca-certificates
  curl
  gnupg
  rsync
  python3
  openssl
  uuid-runtime
  bluez
  dbus
  network-manager
  v4l-utils
  git
)

REQUIRED_GROUPS=(docker video bluetooth dialout plugdev)

ensure_apt_available() {
  if ! command -v apt-get >/dev/null 2>&1; then
    log_error "Instalación automática solo soportada en Debian / Raspberry Pi OS (apt-get)."
    log_error "Instala Docker, bluez y NetworkManager manualmente y vuelve a ejecutar install.sh --skip-host-deps"
    exit 1
  fi
}

apt_install() {
  local packages=("$@")
  log_info "Instalando paquetes: ${packages[*]}"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${packages[@]}"
}

ensure_base_packages() {
  log_info "=== Paquetes base del sistema ==="
  apt-get update -qq
  apt_install "${HOST_APT_PACKAGES[@]}"
}

ensure_docker_installed() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log_info "Docker ya instalado: $(docker --version | head -1)"
    log_info "Compose: $(docker compose version 2>/dev/null | head -1)"
    return 0
  fi

  log_info "=== Docker Engine + Compose v2 ==="
  log_info "Descargando instalador oficial (get.docker.com)..."

  local installer
  installer="$(mktemp /tmp/get-docker.XXXXXX.sh)"
  curl -fsSL https://get.docker.com -o "${installer}"
  chmod +x "${installer}"

  if ! sh "${installer}"; then
    rm -f "${installer}"
    log_error "Falló la instalación de Docker. Revisa conexión a Internet y repositorios apt."
    exit 1
  fi
  rm -f "${installer}"

  if ! command -v docker >/dev/null 2>&1; then
    log_error "Docker no disponible tras la instalación."
    exit 1
  fi

  if ! docker compose version >/dev/null 2>&1; then
    log_info "Instalando plugin docker-compose-plugin..."
    apt-get update -qq
    apt_install docker-compose-plugin
  fi

  log_info "Docker instalado: $(docker --version | head -1)"
  log_info "Compose: $(docker compose version 2>/dev/null | head -1)"
}

ensure_bluezero_dbus_policy() {
  local policy_src=""
  local candidate
  for candidate in \
    "${REPO_ROOT:-}/deploy/dbus/ukBaz.bluezero.conf" \
    "${INSTALL_DIR:-}/deploy/dbus/ukBaz.bluezero.conf"; do
    if [[ -f "${candidate}" ]]; then
      policy_src="${candidate}"
      break
    fi
  done
  local policy_dst="/etc/dbus-1/system.d/ukBaz.bluezero.conf"

  if [[ -z "${policy_src}" ]]; then
    log_warn "Política D-Bus bluezero no encontrada — BLE GATT puede fallar"
    return 0
  fi

  if [[ ! -f "${policy_dst}" ]] || ! cmp -s "${policy_src}" "${policy_dst}"; then
    log_info "Instalando política D-Bus bluezero: ${policy_dst}"
    cp "${policy_src}" "${policy_dst}"
    chmod 644 "${policy_dst}"
    systemctl reload dbus.service 2>/dev/null || true
  fi
}

ensure_bluez_experimental() {
  local conf="/etc/bluetooth/main.conf"
  local changed=false

  if [[ ! -f "${conf}" ]]; then
    log_warn "No se encontró ${conf} — omitiendo Experimental=true"
    return 0
  fi

  if grep -qE '^[[:space:]]*Experimental[[:space:]]*=' "${conf}"; then
    if grep -qE '^[[:space:]]*Experimental[[:space:]]*=[[:space:]]*true' "${conf}"; then
      log_info "BlueZ Experimental=true ya configurado"
      return 0
    fi
    sed -i 's/^[[:space:]]*Experimental[[:space:]]*=.*/Experimental = true/' "${conf}"
    changed=true
  elif grep -q '^\[General\]' "${conf}"; then
    sed -i '/^\[General\]/a Experimental = true' "${conf}"
    changed=true
  else
    printf '\n[General]\nExperimental = true\n' >> "${conf}"
    changed=true
  fi

  if [[ "${changed}" == true ]]; then
    log_info "BlueZ: Experimental=true (peripheral BLE GATT en Pi Zero W2)"
    systemctl restart bluetooth.service 2>/dev/null || true
  fi
}

ensure_run_user_groups() {
  local run_user="$1"

  if [[ -z "${run_user}" ]] || ! id "${run_user}" >/dev/null 2>&1; then
    log_warn "Usuario ${run_user:-?} no existe — omitiendo grupos"
    return 0
  fi

  local group
  for group in "${REQUIRED_GROUPS[@]}"; do
    ensure_group_exists "${group}"
    if getent group "${group}" >/dev/null 2>&1; then
      usermod -aG "${group}" "${run_user}" 2>/dev/null || true
    fi
  done

  log_info "Usuario '${run_user}' en grupos: ${REQUIRED_GROUPS[*]} (los que existan)"
}

ensure_system_services() {
  log_info "=== Servicios del sistema ==="

  ensure_bluezero_dbus_policy
  ensure_bluez_experimental

  systemctl enable dbus.service 2>/dev/null || true
  systemctl start dbus.service 2>/dev/null || true

  systemctl enable docker.service
  systemctl start docker.service

  if systemctl list-unit-files bluetooth.service >/dev/null 2>&1; then
    systemctl enable bluetooth.service
    systemctl start bluetooth.service
    log_info "Bluetooth (bluez) activado"
  else
    log_warn "Servicio bluetooth.service no encontrado"
  fi

  if systemctl list-unit-files NetworkManager.service >/dev/null 2>&1; then
    systemctl enable NetworkManager.service
    systemctl start NetworkManager.service
    log_info "NetworkManager activado (WiFi vía nmcli)"
  else
    log_warn "NetworkManager no disponible — WiFi desde la app puede no funcionar"
  fi
}

verify_host_ready() {
  log_info "=== Verificación del host ==="

  require_command docker
  require_command python3
  require_command rsync

  if ! docker compose version >/dev/null 2>&1; then
    log_error "Docker Compose v2 no disponible tras la instalación."
    exit 1
  fi

  if ! docker info >/dev/null 2>&1; then
    log_error "Docker daemon no responde. Prueba: systemctl status docker"
    exit 1
  fi

  if [[ ! -S /var/run/dbus/system_bus_socket ]]; then
    log_error "No hay socket D-Bus del sistema — BLE/WiFi en contenedor fallarán."
    exit 1
  fi

  log_info "Host listo para NiloCardmed."
}

verify_service_and_container() {
  local service="${NILOCARDMED_SERVICE_NAME:-nilocardmed}"
  local attempt

  log_info "=== Verificación post-instalación ==="

  for attempt in $(seq 1 45); do
    if systemctl is-failed --quiet "${service}.service"; then
      log_error "Servicio ${service} en estado failed"
      show_compose_startup_diagnostics
      return 1
    fi
    if systemctl is-active --quiet "${service}.service"; then
      break
    fi
    sleep 2
  done

  if ! systemctl is-active --quiet "${service}.service"; then
    log_error "Servicio ${service} no arrancó a tiempo"
    show_compose_startup_diagnostics
    return 1
  fi

  if ! run_compose_in_install_dir ps --status running 2>/dev/null | grep -q nilocardmed; then
    log_warn "Contenedor aún no aparece como running; esperando..."
    sleep 5
  fi

  if run_compose_in_install_dir ps 2>/dev/null | grep -qE 'Up|running'; then
    log_info "Contenedor Docker en ejecución"
    run_compose_in_install_dir ps || true
    return 0
  fi

  log_error "El contenedor no está en ejecución"
  show_compose_startup_diagnostics
  return 1
}

show_compose_startup_diagnostics() {
  local service="${NILOCARDMED_SERVICE_NAME:-nilocardmed}"

  log_error "=== Diagnóstico (journal + compose) ==="
  journalctl -u "${service}.service" -n 60 --no-pager 2>/dev/null || true
  log_info "COMPOSE_FILE=${COMPOSE_FILE:-?}"
  if [[ -n "${INSTALL_DIR:-}" && -d "${INSTALL_DIR}" ]]; then
    (
      cd "${INSTALL_DIR}"
      export COMPOSE_PROJECT_NAME COMPOSE_FILE
      # shellcheck disable=SC1090
      set -a && source "${DEPLOY_ENV_FILE:-${INSTALL_DIR}/deploy.env}" && set +a
      log_info "--- docker compose config ---"
      ${DOCKER_COMPOSE_CMD} config 2>&1 | tail -30 || true
      log_info "--- docker compose ps -a ---"
      run_compose_in_install_dir ps -a 2>&1 || true
      log_info "--- logs contenedor (últimas 40 líneas) ---"
      run_compose_in_install_dir logs --tail=40 nilocardmed 2>&1 || true
    )
  fi
}

install_host_dependencies() {
  local run_user="${1:-${SUDO_USER:-$(id -un)}}"

  if is_true "${SKIP_HOST_DEPS:-false}"; then
    log_info "SKIP_HOST_DEPS=true — omitiendo instalación de paquetes del host"
    ensure_run_user_groups "${run_user}"
    ensure_bluez_experimental
    verify_host_ready
    return 0
  fi

  log_info "=== Instalación de dependencias del host (usuario: ${run_user}) ==="
  log_info "Puede tardar varios minutos en Pi Zero (apt + Docker)..."

  ensure_apt_available
  ensure_base_packages
  ensure_docker_installed
  ensure_system_services
  ensure_run_user_groups "${run_user}"
  verify_host_ready

  log_info "=== Dependencias del host completadas ==="
}
