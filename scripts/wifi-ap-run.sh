#!/usr/bin/env bash
# Arranca o detiene el AP WiFi concurrente (uap0) en la Pi.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
CONFIG_DIR="/etc/nilocardmed/wifi-ap"
RUN_DIR="/run/nilocardmed/wifi-ap"
LOG_DIR="/var/log/nilocardmed/wifi-ap"

STA_INTERFACE="${WIFI_STA_INTERFACE:-wlan0}"
AP_INTERFACE="${WIFI_AP_INTERFACE:-uap0}"
AP_IP="${WIFI_AP_IP:-192.168.4.1}"
AP_CIDR="${WIFI_AP_CIDR:-24}"
AP_SSID_PREFIX="${WIFI_AP_SSID_PREFIX:-Nilocardmed-Config}"
WIFI_COUNTRY="${WIFI_COUNTRY_CODE:-ES}"

log() { echo "[nilocardmed-wifi-ap] $*"; }
log_warn() { echo "[nilocardmed-wifi-ap][AVISO] $*" >&2; }
log_error() { echo "[nilocardmed-wifi-ap][ERROR] $*" >&2; }

read_env_value() {
  local file="$1"
  local key="$2"
  [[ -f "${file}" ]] || return 1
  grep -E "^${key}=" "${file}" 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/^"//;s/"$//' || return 1
}

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

ensure_wifi_country() {
  if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_wifi_country "${WIFI_COUNTRY}" 2>/dev/null || true
  fi
  if command -v iw >/dev/null 2>&1; then
    iw reg set "${WIFI_COUNTRY}" 2>/dev/null || true
  fi
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
  for attempt in $(seq 1 30); do
    if [[ -d "/sys/class/net/${STA_INTERFACE}" ]]; then
      return 0
    fi
    sleep 2
  done
  log_warn "Interfaz ${STA_INTERFACE} no disponible tras 60 s"
  return 1
}

ensure_ap_interface() {
  mkdir -p "${LOG_DIR}"
  if ip link show "${AP_INTERFACE}" >/dev/null 2>&1; then
    return 0
  fi
  if ! ip link show "${STA_INTERFACE}" >/dev/null 2>&1; then
    log_error "No existe ${STA_INTERFACE}"
    return 1
  fi
  log "Creando interfaz virtual ${AP_INTERFACE} (__ap) sobre ${STA_INTERFACE}"
  if iw dev "${STA_INTERFACE}" interface add "${AP_INTERFACE}" type __ap 2>"${LOG_DIR}/iw-add.err"; then
    return 0
  fi
  log_error "iw no pudo crear ${AP_INTERFACE}: $(tr '\n' ' ' <"${LOG_DIR}/iw-add.err" 2>/dev/null || echo '?')"
  log_error "Comprueba país WiFi (raspi-config) y soporte concurrente: iw list | grep -A4 'valid interface combinations'"
  return 1
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
  local password="$3"
  local ieee11n="${4:-1}"
  local conf="${CONFIG_DIR}/hostapd.conf"

  if [[ -z "${password}" || ${#password} -lt 8 || ${#password} -gt 63 ]]; then
    log_error "Contraseña WPA inválida (8-63 chars). Ejecuta: sudo ./scripts/update.sh"
    return 1
  fi

  mkdir -p "${CONFIG_DIR}" "${LOG_DIR}"
  python3 - "${conf}" "${AP_INTERFACE}" "${ssid}" "${channel}" "${WIFI_COUNTRY}" "${password}" "${ieee11n}" <<'PY'
import sys
from pathlib import Path

path, interface, ssid, channel, country, password, ieee11n = sys.argv[1:8]
lines = [
    f"interface={interface}",
    "driver=nl80211",
    f"ssid={ssid}",
    "hw_mode=g",
    f"channel={channel}",
    f"country_code={country}",
    f"ieee80211n={ieee11n}",
    "wmm_enabled=1",
    "macaddr_acl=0",
    "auth_algs=1",
    "ignore_broadcast_ssid=0",
    "wpa=2",
    "wpa_key_mgmt=WPA-PSK",
    f"wpa_passphrase={password}",
    "rsn_pairwise=CCMP",
]
Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
  log "hostapd.conf escrito (WPA2, canal ${channel})"
}

write_dnsmasq_conf() {
  local conf="${CONFIG_DIR}/dnsmasq.conf"
  cat >"${conf}" <<EOF
interface=${AP_INTERFACE}
bind-interfaces
except-interface=lo
listen-address=${AP_IP}
port=0
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,12h
dhcp-option=3,${AP_IP}
dhcp-option=6,${AP_IP}
no-hosts
no-resolv
log-facility=${LOG_DIR}/dnsmasq.log
EOF
}

start_hostapd() {
  local conf="${CONFIG_DIR}/hostapd.conf"
  if ! hostapd -t "${conf}" >"${LOG_DIR}/hostapd-test.log" 2>&1; then
    log_warn "hostapd -t falló (ieee80211n=1): $(tail -3 "${LOG_DIR}/hostapd-test.log" | tr '\n' ' ')"
    return 1
  fi
  hostapd -B -P "${RUN_DIR}/hostapd.pid" "${conf}" >"${LOG_DIR}/hostapd.log" 2>&1
  sleep 1
  if [[ -f "${RUN_DIR}/hostapd.pid" ]] && kill -0 "$(cat "${RUN_DIR}/hostapd.pid")" 2>/dev/null; then
    return 0
  fi
  log_warn "hostapd terminó: $(tail -5 "${LOG_DIR}/hostapd.log" | tr '\n' ' ')"
  return 1
}

start_dnsmasq() {
  dnsmasq -C "${CONFIG_DIR}/dnsmasq.conf" -x "${RUN_DIR}/dnsmasq.pid" \
    >"${LOG_DIR}/dnsmasq-start.log" 2>&1 || {
    log_error "dnsmasq falló: $(tail -5 "${LOG_DIR}/dnsmasq-start.log" | tr '\n' ' ')"
    return 1
  }
}

start_ap() {
  load_deploy_env
  mkdir -p "${RUN_DIR}" "${LOG_DIR}"

  local ap_password ssid channel suffix
  ap_password="$(resolve_ap_password || true)"
  if [[ -z "${ap_password}" ]]; then
    log_error "Sin contraseña WPA en ${INSTALL_DIR}/.env (NILOCARDMED_CONNECTION_PASSWORD)"
    log_error "Ejecuta en la Pi: cd ~/dev/NiloCardmed-dev && sudo ./scripts/update.sh"
    log_error "Debe ser ≥8 caracteres (WPA2). Comprueba: grep CONNECTION /opt/nilocardmed/.env"
    exit 1
  fi
  if [[ ${#ap_password} -lt 8 || ${#ap_password} -gt 63 ]]; then
    log_error "Contraseña WPA inválida (${#ap_password} chars; necesita 8-63). Ejecuta: sudo ./scripts/update.sh"
    exit 1
  fi

  wait_for_sta || true
  ensure_wifi_country

  suffix="$(mac_suffix)"
  ssid="${AP_SSID_PREFIX}-${suffix}"
  channel="$(detect_channel)"

  ensure_ap_interface || exit 1
  configure_ap_address
  write_dnsmasq_conf

  write_hostapd_conf "${ssid}" "${channel}" "${ap_password}" "1"
  if ! start_hostapd; then
    log_warn "Reintentando hostapd sin ieee80211n..."
    write_hostapd_conf "${ssid}" "${channel}" "${ap_password}" "0"
    start_hostapd || {
      log_error "hostapd no arrancó — log: ${LOG_DIR}/hostapd.log"
      exit 1
    }
  fi

  if [[ -f "${RUN_DIR}/dnsmasq.pid" ]] && kill -0 "$(cat "${RUN_DIR}/dnsmasq.pid")" 2>/dev/null; then
    :
  else
    start_dnsmasq || exit 1
  fi

  log "AP activo: SSID=${ssid} IP=${AP_IP} (${AP_INTERFACE}) canal=${channel} WPA2"
}

stop_ap() {
  load_deploy_env
  if [[ -f "${RUN_DIR}/dnsmasq.pid" ]]; then
    kill "$(cat "${RUN_DIR}/dnsmasq.pid")" 2>/dev/null || true
    rm -f "${RUN_DIR}/dnsmasq.pid"
  fi
  pkill -f "dnsmasq -C ${CONFIG_DIR}/dnsmasq.conf" 2>/dev/null || true

  if [[ -f "${RUN_DIR}/hostapd.pid" ]]; then
    kill "$(cat "${RUN_DIR}/hostapd.pid")" 2>/dev/null || true
    rm -f "${RUN_DIR}/hostapd.pid"
  fi
  pkill -f "${CONFIG_DIR}/hostapd.conf" 2>/dev/null || true

  if ip link show "${AP_INTERFACE}" >/dev/null 2>&1; then
    ip link set "${AP_INTERFACE}" down 2>/dev/null || true
    iw dev "${AP_INTERFACE}" del 2>/dev/null || true
  fi
  log "AP detenido"
}

status_ap() {
  load_deploy_env
  ip -br addr show "${AP_INTERFACE}" 2>/dev/null || echo "${AP_INTERFACE}: no existe"
  iw dev "${AP_INTERFACE}" info 2>/dev/null || true
  if [[ -f "${RUN_DIR}/hostapd.pid" ]]; then
    echo "hostapd pid: $(cat "${RUN_DIR}/hostapd.pid")"
  fi
  if [[ -f "${RUN_DIR}/dnsmasq.pid" ]]; then
    echo "dnsmasq pid: $(cat "${RUN_DIR}/dnsmasq.pid")"
  fi
}

diagnose_ap() {
  load_deploy_env
  log "=== Diagnóstico WiFi AP ==="
  log "INSTALL_DIR=${INSTALL_DIR}"
  log "País WiFi: $(iw reg get 2>/dev/null | head -3 | tr '\n' ' ')"
  log "Contraseña WPA: $(resolve_ap_password >/dev/null && echo 'OK (configurada)' || echo 'FALTA')"
  ip -br link show "${STA_INTERFACE}" 2>/dev/null || log_warn "Sin ${STA_INTERFACE}"
  ip -br link show "${AP_INTERFACE}" 2>/dev/null || log_warn "Sin ${AP_INTERFACE}"
  iw dev 2>/dev/null || true
  iw list 2>/dev/null | grep -A6 "valid interface combinations" || true
  systemctl status nilocardmed-wifi-ap --no-pager 2>/dev/null || true
  [[ -f "${LOG_DIR}/hostapd.log" ]] && tail -10 "${LOG_DIR}/hostapd.log" || true
  [[ -f "${LOG_DIR}/iw-add.err" ]] && cat "${LOG_DIR}/iw-add.err" || true
}

cmd="${1:-start}"
case "${cmd}" in
  start) start_ap ;;
  stop) stop_ap ;;
  restart) stop_ap; start_ap ;;
  status) status_ap ;;
  diagnose) diagnose_ap ;;
  *)
    echo "Uso: $0 {start|stop|restart|status|diagnose}" >&2
    exit 1
    ;;
esac
