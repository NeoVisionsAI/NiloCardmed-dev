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

# Evita colgar install/update si systemctl/bluetoothctl quedan esperando D-Bus.
run_with_timeout() {
  local seconds="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "${seconds}" "$@" || true
  else
    "$@" || true
  fi
}

# BLUETOOTH_ENABLED (alias) o ENABLE_BLUETOOTH (deploy.env legacy).
# Con ENABLE_WIFI_AP=true el BLE queda off (ahorro RAM/CPU en Pi Zero 2 W).
resolve_bluetooth_enabled() {
  if resolve_wifi_ap_enabled; then
    return 1
  fi
  if is_true "${BLUETOOTH_ENABLED:-}"; then
    return 0
  fi
  if is_true "${ENABLE_BLUETOOTH:-false}"; then
    return 0
  fi
  return 1
}

resolve_wifi_ap_enabled() {
  is_true "${ENABLE_WIFI_AP:-false}"
}

# hostapd/dnsmasq/iw — también en update.sh (--skip-host-deps no instala el resto de apt).
ensure_wifi_ap_packages() {
  if [[ "${EUID}" -ne 0 ]]; then
    return 0
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    log_warn "apt-get no disponible — instala a mano: hostapd dnsmasq iw"
    return 1
  fi

  local need_install=false
  for cmd_pkg in "hostapd:hostapd" "dnsmasq:dnsmasq" "iw:iw"; do
    local cmd="${cmd_pkg%%:*}"
    local pkg="${cmd_pkg#*:}"
    if ! command -v "${cmd}" >/dev/null 2>&1; then
      need_install=true
      log_info "Falta ${cmd} — se instalará paquete ${pkg}"
    fi
  done

  if [[ "${need_install}" != true ]]; then
    return 0
  fi

  log_info "=== Paquetes WiFi AP (hostapd, dnsmasq, iw) ==="
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq hostapd dnsmasq iw
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

  if [[ -f "${install_dir}/deploy.env" ]]; then
    # shellcheck disable=SC1091
    set -a && source "${install_dir}/deploy.env" && set +a
  fi

  if ! resolve_bluetooth_enabled; then
    log_info "Bluetooth deshabilitado (BLUETOOTH_ENABLED/ENABLE_BLUETOOTH=false) — omitiendo ensure-bluetooth-powered"
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

  # Tras tocar BlueZ (p. ej. restart bluetooth), dar tiempo al adaptador.
  sleep 3
}

# Pi 24/7: sin suspender, hibernar ni apagar pantalla por inactividad (requiere root).
ensure_host_always_on() {
  local install_dir="${1:-${INSTALL_DIR:-}}"

  if [[ "${EUID}" -ne 0 ]]; then
    log_debug "ensure_host_always_on: omitido (sin root)"
    return 0
  fi

  local script=""
  for candidate in \
    "${install_dir}/scripts/ensure-host-always-on.sh" \
    "${REPO_ROOT:-}/scripts/ensure-host-always-on.sh" \
    "${SCRIPT_DIR:-}/ensure-host-always-on.sh"; do
    if [[ -n "${candidate}" && -f "${candidate}" ]]; then
      script="${candidate}"
      break
    fi
  done

  if [[ -z "${script}" ]]; then
    log_warn "ensure-host-always-on.sh no encontrado"
    return 0
  fi

  log_info "=== Host always-on (sin suspender / blanking) ==="
  bash "${script}" || log_warn "ensure-host-always-on falló — revisa permisos root"
}

# Solo quita el dropin lightdm roto (seguro en cada update.sh).
ensure_host_lightdm_fix_only() {
  local install_dir="${1:-${INSTALL_DIR:-}}"

  if [[ "${EUID}" -ne 0 ]]; then
    return 0
  fi

  local script=""
  for candidate in \
    "${install_dir}/scripts/ensure-host-always-on.sh" \
    "${REPO_ROOT:-}/scripts/ensure-host-always-on.sh" \
    "${SCRIPT_DIR:-}/ensure-host-always-on.sh"; do
    if [[ -n "${candidate}" && -f "${candidate}" ]]; then
      script="${candidate}"
      break
    fi
  done

  if [[ -z "${script}" ]]; then
    return 0
  fi

  NILOCARDMED_HOST_TUNING_LIGHT=true bash "${script}" || true
}

# Swap mínima 1 GB en /var/swap si la Pi trae poca (Pi Zero 2 W). Ver docs/INCREASE_SWAP.md
ensure_host_swap() {
  local install_dir="${1:-${INSTALL_DIR:-}}"

  if [[ "${EUID}" -ne 0 ]]; then
    log_debug "ensure_host_swap: omitido (sin root)"
    return 0
  fi

  local script=""
  for candidate in \
    "${install_dir}/scripts/ensure-host-swap.sh" \
    "${REPO_ROOT:-}/scripts/ensure-host-swap.sh" \
    "${SCRIPT_DIR:-}/ensure-host-swap.sh"; do
    if [[ -n "${candidate}" && -f "${candidate}" ]]; then
      script="${candidate}"
      break
    fi
  done

  if [[ -z "${script}" ]]; then
    log_warn "ensure-host-swap.sh no encontrado"
    return 0
  fi

  if [[ -f "${install_dir}/deploy.env" ]]; then
    # shellcheck disable=SC1091
    set -a && source "${install_dir}/deploy.env" && set +a
  fi
  if ! is_true "${ENABLE_HOST_SWAP:-true}"; then
    log_info "ENABLE_HOST_SWAP=false — omitiendo ensure-host-swap"
    return 0
  fi

  bash "${script}" || log_warn "ensure-host-swap falló — revisa espacio en /var"
}

# DISABLE_GUI / OPTIMIZE_GPU_MEM en deploy.env (Pi Zero 2 W). Ver deploy.env.example
ensure_host_memory_optimize() {
  local install_dir="${1:-${INSTALL_DIR:-}}"

  if [[ "${EUID}" -ne 0 ]]; then
    log_debug "ensure_host_memory_optimize: omitido (sin root)"
    return 0
  fi

  if [[ -f "${install_dir}/deploy.env" ]]; then
    # shellcheck disable=SC1091
    set -a && source "${install_dir}/deploy.env" && set +a
  fi

  local script=""
  for candidate in \
    "${install_dir}/scripts/ensure-host-memory-optimize.sh" \
    "${REPO_ROOT:-}/scripts/ensure-host-memory-optimize.sh" \
    "${SCRIPT_DIR:-}/ensure-host-memory-optimize.sh"; do
    if [[ -n "${candidate}" && -f "${candidate}" ]]; then
      script="${candidate}"
      break
    fi
  done

  if [[ -z "${script}" ]]; then
    log_warn "ensure-host-memory-optimize.sh no encontrado"
    return 0
  fi

  bash "${script}" || log_warn "ensure-host-memory-optimize falló"
}

# Punto de acceso WiFi concurrente (uap0 + hostapd + dnsmasq). Requiere root.
ensure_wifi_ap_host_ready() {
  local install_dir="${1:-${INSTALL_DIR:-}}"

  if [[ -z "${install_dir}" ]]; then
    log_warn "ensure_wifi_ap_host_ready: INSTALL_DIR no definido"
    return 0
  fi

  if [[ -f "${install_dir}/deploy.env" ]]; then
    # shellcheck disable=SC1091
    set -a && source "${install_dir}/deploy.env" && set +a
  fi

  if ! resolve_wifi_ap_enabled; then
    log_info "ENABLE_WIFI_AP=false — omitiendo AP WiFi de aprovisionamiento"
    return 0
  fi

  local script="${install_dir}/scripts/ensure-wifi-ap.sh"
  if [[ ! -f "${script}" ]]; then
    log_warn "No encontrado: ${script}"
    return 0
  fi

  log_info "=== WiFi AP concurrente (Nilocardmed-Config, 192.168.4.1) ==="
  INSTALL_DIR="${install_dir}"   bash "${script}" || log_warn "ensure-wifi-ap falló — revisa hostapd/dnsmasq"
}

# Apaga BlueZ/rfkill cuando el aprovisionamiento es por WiFi AP (ahorro memoria).
ensure_bluetooth_disabled_for_wifi_ap() {
  local install_dir="${1:-${INSTALL_DIR:-}}"

  if [[ -f "${install_dir}/deploy.env" ]]; then
    # shellcheck disable=SC1091
    set -a && source "${install_dir}/deploy.env" && set +a
  fi

  if ! resolve_wifi_ap_enabled; then
    return 0
  fi

  if [[ "${EUID}" -ne 0 ]]; then
    return 0
  fi

  log_info "=== Bluetooth off (modo WiFi AP activo — ahorro RAM/CPU) ==="

  if [[ -f /etc/bluetooth/main.conf ]]; then
    if grep -qE '^AutoEnable\s*=' /etc/bluetooth/main.conf 2>/dev/null; then
      sed -i 's/^AutoEnable\s*=.*/AutoEnable=false/' /etc/bluetooth/main.conf
    elif grep -qE '^\[General\]' /etc/bluetooth/main.conf 2>/dev/null; then
      sed -i '/^\[General\]/a AutoEnable=false' /etc/bluetooth/main.conf
    fi
    log_info "BlueZ: AutoEnable=false (evita reencendido automático)"
  fi

  # rfkill primero (instantáneo; no usa D-Bus — evita colgar el script)
  if command -v rfkill >/dev/null 2>&1; then
    run_with_timeout 2 rfkill block bluetooth
    log_info "rfkill: bluetooth bloqueado"
  fi

  if command -v hciconfig >/dev/null 2>&1; then
    run_with_timeout 2 hciconfig hci0 down
  fi

  if systemctl list-unit-files bluetooth.service >/dev/null 2>&1; then
    log_info "Deteniendo bluetooth.service (timeout 8 s)..."
    run_with_timeout 2 systemctl kill --signal=SIGKILL bluetooth.service 2>/dev/null || true
    run_with_timeout 8 systemctl stop bluetooth.service 2>/dev/null || true
    run_with_timeout 5 systemctl disable bluetooth.service 2>/dev/null || true
    run_with_timeout 5 systemctl mask bluetooth.service 2>/dev/null || true
  fi

  for unit in blueman-mechanism.service blueman-applet.service; do
    if systemctl list-unit-files "${unit}" >/dev/null 2>&1; then
      run_with_timeout 5 systemctl stop "${unit}"
      run_with_timeout 5 systemctl disable "${unit}"
    fi
  done

  pkill -x blueman-applet 2>/dev/null || true

  # No usar bluetoothctl/btmgmt aquí: cuelgan si bluetoothd está stopping/killed.
  log_info "Bluetooth: servicio enmascarado + rfkill (sin GATT en Pi)"
  log_info "Icono del panel: puede mentir hasta cerrar sesión; comprueba: rfkill list bluetooth"
}

restart_wifi_ap_if_enabled() {
  local install_dir="${1:-${INSTALL_DIR:-}}"

  if [[ -f "${install_dir}/deploy.env" ]]; then
    # shellcheck disable=SC1091
    set -a && source "${install_dir}/deploy.env" && set +a
  fi

  if ! resolve_wifi_ap_enabled || [[ "${EUID}" -ne 0 ]]; then
    return 0
  fi

  if systemctl list-unit-files nilocardmed-wifi-ap.service >/dev/null 2>&1; then
    log_info "Reiniciando AP WiFi (aplica WPA / hostapd)..."
    if ! run_with_timeout 90 systemctl restart nilocardmed-wifi-ap.service; then
      log_warn "nilocardmed-wifi-ap falló al reiniciar — diagnóstico:"
      local ap_script="${install_dir}/scripts/wifi-ap-run.sh"
      if [[ -f "${ap_script}" ]]; then
        INSTALL_DIR="${install_dir}" bash "${ap_script}" diagnose 2>&1 || true
      fi
      journalctl -u nilocardmed-wifi-ap -n 20 --no-pager 2>/dev/null || true
      log_warn "Prueba: sudo systemctl restart nilocardmed-wifi-ap"
      log_warn "Logs: /var/log/nilocardmed/wifi-ap/hostapd.log"
    fi
  fi
}

verify_http_provisioning() {
  local install_dir="${1:-${INSTALL_DIR:-}}"

  if [[ -f "${install_dir}/deploy.env" ]]; then
    # shellcheck disable=SC1091
    set -a && source "${install_dir}/deploy.env" && set +a
  fi

  if ! resolve_wifi_ap_enabled; then
    return 0
  fi

  local env_file="${install_dir}/.env"
  if [[ -f "${env_file}" ]] && ! grep -qE '^NILOCARDMED_HTTP__ENABLED=true' "${env_file}" 2>/dev/null; then
    log_warn "NILOCARDMED_HTTP__ENABLED no está en true en ${env_file}"
  fi

  if ! command -v docker >/dev/null 2>&1; then
    return 0
  fi

  local cname="${NILOCARDMED_CONTAINER_NAME:-nilocardmed}"
  if ! docker ps --format '{{.Names}}' | grep -qx "${cname}"; then
    log_warn "Contenedor ${cname} no está en ejecución — HTTP no disponible"
    return 0
  fi

  if ! docker exec "${cname}" python -c "from nilocardmed.http.server import HttpProvisioningService" >/dev/null 2>&1; then
    log_error "La imagen Docker no incluye el servidor HTTP WiFi."
    log_error "Ejecuta: sudo ./scripts/update.sh --build  (una vez; ~1-3 min con caché)"
    return 1
  fi

  if curl -sf --connect-timeout 5 "http://127.0.0.1:8080/api/status" >/dev/null 2>&1; then
    log_info "HTTP aprovisionamiento OK → http://192.168.4.1:8080/api/status"
    return 0
  fi

  log_warn "HTTP :8080 no responde aún — espera 10 s y prueba: curl http://127.0.0.1:8080/api/status"
  log_warn "Revisa: sudo docker logs ${cname} 2>&1 | tail -30"
  return 1
}
