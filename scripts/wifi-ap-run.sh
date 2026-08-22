#!/usr/bin/env bash
# Arranca o detiene el AP WiFi concurrente (uap0) en la Pi.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
CONFIG_DIR="/etc/nilocardmed/wifi-ap"
RUN_DIR="/run/nilocardmed/wifi-ap"

STA_INTERFACE="${WIFI_STA_INTERFACE:-wlan0}"
AP_INTERFACE="${WIFI_AP_INTERFACE:-uap0}"
AP_IP="${WIFI_AP_IP:-192.168.4.1}"
AP_CIDR="${WIFI_AP_CIDR:-24}"
AP_SSID_PREFIX="${WIFI_AP_SSID_PREFIX:-Nilocardmed-Config}"
WIFI_COUNTRY="${WIFI_COUNTRY_CODE:-ES}"
AP_PASSWORD="${WIFI_AP_PASSWORD:-}"

read_env_value() {
  local file="$1"
  local key="$2"
  [[ -f "${file}" ]] || return 1
  grep -E "^${key}=" "${file}" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || return 1
}

resolve_ap_password() {
  local pwd="${WIFI_AP_PASSWORD:-}"
  pwd="${pwd#"${pwd%%[![:space:]]*}"}"
  pwd="${pwd%"${pwd##*[![:space:]]}"}"
  if [[ -n "${pwd}" && "${pwd}" != "changeme" ]]; then
    echo "${pwd}"
    return 0
  fi
  pwd="$(read_env_value "${INSTALL_DIR}/.env" "NILOCARDMED_CONNECTION_PASSWORD" || true)"
  if [[ -n "${pwd}" && "${pwd}" != "changeme" ]]; then
    echo "${pwd}"
    return 0
  fi
  pwd="$(read_env_value "${INSTALL_DIR}/.env" "NILOCARDMED_BLUETOOTH__PASSWORD" || true)"
  if [[ -n "${pwd}" && "${pwd}" != "changeme" ]]; then
    echo "${pwd}"
    return 0
  fi
  return 1
}

log() { echo "[nilocardmed-wifi-ap] $*"; }
log_warn() { echo "[nilocardmed-wifi-ap][AVISO] $*" >&2; }

load_deploy_env() {
  if [[ -f "${INSTALL_DIR}/deploy.env" ]]; then
    # shellcheck disable=SC1091
    set -a && source "${INSTALL_DIR}/deploy.env" && set +a
  fi
  STA_INTERFACE="${WIFI_STA_INTERFACE:-wlan0}"
  AP_INTERFACE="${WIFI_AP_INTERFACE:-uap0}"
  AP_IP="${WIFI_AP_IP:-192.168.4.1}"
  AP_CIDR="${WIFI_AP_CIDR:-24}"
  AP_SSID_PREFIX="${WIFI_AP_SSID_PREFIX:-Nilocardmed-Config}"
  WIFI_COUNTRY="${WIFI_COUNTRY_CODE:-ES}"
  AP_PASSWORD="${WIFI_AP_PASSWORD:-}"
}

mac_suffix() {
  local mac=""
  mac="$(cat "/sys/class/net/${STA_INTERFACE}/address" 2>/dev/null || true)"
  mac="${mac//:/}"
  if [[ -n "${mac}" ]]; then
    echo "${mac: -4}"
    return 0
  fi
  echo "0000"
}

detect_channel() {
  local channel=""
  channel="$(iw dev "${STA_INTERFACE}" info 2>/dev/null | awk '/channel/ {print $2; exit}' || true)"
  if [[ -n "${channel}" && "${channel}" =~ ^[0-9]+$ ]]; then
    echo "${channel}"
    return 0
  fi
  echo "6"
}

wait_for_sta() {
  local attempt
  for attempt in $(seq 1 60); do
    if [[ -d "/sys/class/net/${STA_INTERFACE}" ]]; then
      return 0
    fi
    sleep 2
  done
  log_warn "Interfaz ${STA_INTERFACE} no disponible tras 120 s"
  return 1
}

ensure_ap_interface() {
  if ip link show "${AP_INTERFACE}" >/dev/null 2>&1; then
    return 0
  fi
  if ! ip link show "${STA_INTERFACE}" >/dev/null 2>&1; then
    log_warn "No existe ${STA_INTERFACE}; no se puede crear ${AP_INTERFACE}"
    return 1
  fi
  log "Creando interfaz virtual ${AP_INTERFACE} (__ap) sobre ${STA_INTERFACE}"
  iw dev "${STA_INTERFACE}" interface add "${AP_INTERFACE}" type __ap
}

configure_ap_address() {
  ip link set "${AP_INTERFACE}" down 2>/dev/null || true
  ip addr flush dev "${AP_INTERFACE}" 2>/dev/null || true
  ip addr add "${AP_IP}/${AP_CIDR}" dev "${AP_INTERFACE}"
  ip link set "${AP_INTERFACE}" up
}

write_hostapd_conf() {
  local ssid="$1"
  local channel="$2"
  local password="${3:-}"
  local conf="${CONFIG_DIR}/hostapd.conf"

  if [[ -z "${password}" || ${#password} -lt 8 || ${#password} -gt 63 ]]; then
    log_warn "Sin contraseña WPA válida (8-63 chars en WIFI_AP_PASSWORD / NILOCARDMED_CONNECTION_PASSWORD)"
    log_warn "Ejecuta: sudo ./scripts/update.sh  (pedirá contraseña de aprovisionamiento)"
    return 1
  fi

  mkdir -p "${CONFIG_DIR}"
  cat >"${conf}" <<EOF
interface=${AP_INTERFACE}
driver=nl80211
ssid=${ssid}
hw_mode=g
channel=${channel}
country_code=${WIFI_COUNTRY}
ieee80211n=1
wmm_enabled=1
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_key_mgmt=WPA-PSK
wpa_passphrase=${password}
rsn_pairwise=CCMP
EOF
  log "AP WPA2-PSK activo (la tablet debe introducir esta contraseña al unirse)"
}

write_dnsmasq_conf() {
  local conf="${CONFIG_DIR}/dnsmasq.conf"
  cat >"${conf}" <<EOF
interface=${AP_INTERFACE}
bind-interfaces
except-interface=lo
listen-address=${AP_IP}
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,12h
dhcp-option=3,${AP_IP}
dhcp-option=6,${AP_IP}
no-hosts
no-resolv
EOF
}

start_ap() {
  load_deploy_env
  mkdir -p "${RUN_DIR}"
  wait_for_sta || true

  local suffix ssid channel ap_password
  suffix="$(mac_suffix)"
  ssid="${AP_SSID_PREFIX}-${suffix}"
  channel="$(detect_channel)"
  ap_password="$(resolve_ap_password || true)"
  if [[ -z "${ap_password}" ]]; then
    log_warn "No hay contraseña para WPA — abortando AP abierto"
    log_warn "Ejecuta: sudo ./scripts/update.sh  (pedirá contraseña de aprovisionamiento)"
    exit 1
  fi

  ensure_ap_interface
  configure_ap_address
  write_hostapd_conf "${ssid}" "${channel}" "${ap_password}" || exit 1
  write_dnsmasq_conf

  if [[ -f "${RUN_DIR}/hostapd.pid" ]] && kill -0 "$(cat "${RUN_DIR}/hostapd.pid")" 2>/dev/null; then
    log "hostapd ya en ejecución (pid $(cat "${RUN_DIR}/hostapd.pid"))"
  else
    log "Iniciando hostapd SSID=${ssid} canal=${channel} (mismo canal que ${STA_INTERFACE})"
    hostapd -B -P "${RUN_DIR}/hostapd.pid" "${CONFIG_DIR}/hostapd.conf"
  fi

  if [[ -f "${RUN_DIR}/dnsmasq.pid" ]] && kill -0 "$(cat "${RUN_DIR}/dnsmasq.pid")" 2>/dev/null; then
    log "dnsmasq ya en ejecución (pid $(cat "${RUN_DIR}/dnsmasq.pid"))"
  else
    log "Iniciando dnsmasq DHCP en ${AP_INTERFACE}"
    dnsmasq -C "${CONFIG_DIR}/dnsmasq.conf" -x "${RUN_DIR}/dnsmasq.pid"
  fi

  log "AP activo: ${ssid} → ${AP_IP}/${AP_CIDR} (${AP_INTERFACE})"
}

stop_ap() {
  if [[ -f "${RUN_DIR}/dnsmasq.pid" ]]; then
    kill "$(cat "${RUN_DIR}/dnsmasq.pid")" 2>/dev/null || true
    rm -f "${RUN_DIR}/dnsmasq.pid"
  fi
  pkill -f "dnsmasq -C ${CONFIG_DIR}/dnsmasq.conf" 2>/dev/null || true

  if [[ -f "${RUN_DIR}/hostapd.pid" ]]; then
    kill "$(cat "${RUN_DIR}/hostapd.pid")" 2>/dev/null || true
    rm -f "${RUN_DIR}/hostapd.pid"
  fi
  pkill -f "hostapd -P ${RUN_DIR}/hostapd.pid" 2>/dev/null || true

  if ip link show "${AP_INTERFACE}" >/dev/null 2>&1; then
    ip link set "${AP_INTERFACE}" down 2>/dev/null || true
    iw dev "${AP_INTERFACE}" del 2>/dev/null || true
  fi
  log "AP detenido"
}

status_ap() {
  load_deploy_env
  ip -br addr show "${AP_INTERFACE}" 2>/dev/null || echo "${AP_INTERFACE}: no existe"
  if [[ -f "${RUN_DIR}/hostapd.pid" ]]; then
    echo "hostapd pid: $(cat "${RUN_DIR}/hostapd.pid")"
  fi
  if [[ -f "${RUN_DIR}/dnsmasq.pid" ]]; then
    echo "dnsmasq pid: $(cat "${RUN_DIR}/dnsmasq.pid")"
  fi
}

cmd="${1:-start}"
case "${cmd}" in
  start) start_ap ;;
  stop) stop_ap ;;
  restart) stop_ap; start_ap ;;
  status) status_ap ;;
  *)
    echo "Uso: $0 {start|stop|restart|status}" >&2
    exit 1
    ;;
esac
