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

ensure_system_services() {
  log_info "=== Servicios del sistema ==="

  systemctl enable dbus.service 2>/dev/null || true
  systemctl start dbus.service 2>/dev/null || true

  systemctl enable docker.service
  systemctl start docker.service

  if systemctl list-unit-files bluetooth.service >/dev/null 2>&1; then
    systemctl enable bluetooth.service
    systemctl start bluetooth.service
    log_info "Bluetooth (bluez) activado"
  else
    log_info "Servicio bluetooth.service no encontrado — comprueba paquete bluez"
  fi

  if systemctl list-unit-files NetworkManager.service >/dev/null 2>&1; then
    systemctl enable NetworkManager.service
    systemctl start NetworkManager.service
    log_info "NetworkManager activado (WiFi vía nmcli)"
  else
    log_info "NetworkManager no disponible — WiFi desde la app puede no funcionar"
  fi
}

ensure_run_user_groups() {
  local run_user="$1"

  if [[ -z "${run_user}" ]] || ! id "${run_user}" >/dev/null 2>&1; then
    log_info "Usuario ${run_user:-?} no existe — omitiendo grupos (ajusta NILOCARDMED_RUN_USER en deploy.env)"
    return 0
  fi

  local group
  for group in docker video bluetooth dialout plugdev; do
    if getent group "${group}" >/dev/null 2>&1; then
      usermod -aG "${group}" "${run_user}" 2>/dev/null || true
    fi
  done

  log_info "Usuario '${run_user}' añadido a grupos: docker, video, bluetooth, dialout, plugdev (si existen)"
  log_info "Nota: cierra sesión y vuelve a entrar para que los grupos surtan efecto sin sudo."
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

# Instala todo lo necesario en la Raspberry Pi (idempotente).
# Uso: install_host_dependencies [usuario]
install_host_dependencies() {
  local run_user="${1:-${SUDO_USER:-$(id -un)}}"

  if is_true "${SKIP_HOST_DEPS:-false}"; then
    log_info "SKIP_HOST_DEPS=true — omitiendo instalación de paquetes del host"
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
