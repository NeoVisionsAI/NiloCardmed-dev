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

# Rellenados por prepare_ap_core
AP_SSID=""
AP_CHANNEL=""
AP_PASSWORD=""

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

freq_to_channel() {
  local freq="${1%%.*}"
  if [[ "${freq}" =~ ^[0-9]+$ ]] && (( freq >= 2412 && freq <= 2484 )); then
    echo $(( (freq - 2412) / 5 + 1 ))
    return 0
  fi
  return 1
}

detect_channel() {
  local channel="" freq=""

  channel="$(iw dev "${STA_INTERFACE}" info 2>/dev/null | awk '/channel/ {print $2; exit}' || true)"
  if [[ -n "${channel}" && "${channel}" =~ ^[0-9]+$ ]]; then
    echo "${channel}"
    return 0
  fi

  freq="$(iw dev "${STA_INTERFACE}" link 2>/dev/null | awk '/freq:/ {print $2; exit}' || true)"
  channel="$(freq_to_channel "${freq}" 2>/dev/null || true)"
  if [[ -n "${channel}" ]]; then
    log "Canal desde freq ${freq} MHz → ${channel}"
    echo "${channel}"
    return 0
  fi

  log_warn "wlan0 sin canal — usando 1 (AP+STA deben coincidir en brcmfmac)"
  echo "1"
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

wait_for_sta_channel() {
  local attempt channel=""
  for attempt in $(seq 1 30); do
    channel="$(detect_channel 2>/dev/null || true)"
    if [[ -n "${channel}" && "${channel}" =~ ^[0-9]+$ ]]; then
      log "Canal ${STA_INTERFACE}: ${channel} (AP usará el mismo — requisito Pi concurrente)"
      return 0
    fi
    sleep 2
  done
  log_warn "wlan0 sin canal tras 60 s — conéctala a WiFi antes; el AP puede fallar"
  return 1
}

hostapd_is_running() {
  [[ -f "${RUN_DIR}/hostapd.pid" ]] && kill -0 "$(cat "${RUN_DIR}/hostapd.pid")" 2>/dev/null
}

reset_ap_link() {
  ip link set "${AP_INTERFACE}" down 2>/dev/null || true
  ip addr flush dev "${AP_INTERFACE}" 2>/dev/null || true
}

ensure_ap_interface() {
  mkdir -p "${LOG_DIR}" "${RUN_DIR}" "${CONFIG_DIR}"
  touch "${LOG_DIR}/hostapd.log" "${LOG_DIR}/dnsmasq-start.log" 2>/dev/null || true

  if ip link show "${AP_INTERFACE}" >/dev/null 2>&1; then
    if hostapd_is_running; then
      return 0
    fi
    log_warn "${AP_INTERFACE} existe pero hostapd no corre — recreando interfaz"
    reset_ap_link
    iw dev "${AP_INTERFACE}" del 2>/dev/null || true
  fi
  if ! ip link show "${STA_INTERFACE}" >/dev/null 2>&1; then
    log_error "No existe ${STA_INTERFACE}"
    return 1
  fi
  log "Creando interfaz virtual ${AP_INTERFACE} (__ap) sobre ${STA_INTERFACE}"
  if iw dev "${STA_INTERFACE}" interface add "${AP_INTERFACE}" type __ap 2>"${LOG_DIR}/iw-add.err"; then
    reset_ap_link
    return 0
  fi
  log_error "iw no pudo crear ${AP_INTERFACE}: $(tr '\n' ' ' <"${LOG_DIR}/iw-add.err" 2>/dev/null || echo '?')"
  log_error "Comprueba país WiFi (raspi-config) y soporte concurrente: iw list | grep -A4 'valid interface combinations'"
  return 1
}

assign_ap_ip() {
  if ! ip addr show dev "${AP_INTERFACE}" 2>/dev/null | grep -qE "inet ${AP_IP}/"; then
    ip addr add "${AP_IP}/${AP_CIDR}" dev "${AP_INTERFACE}" 2>/dev/null || true
  fi
  ip link set "${AP_INTERFACE}" up 2>/dev/null || true
  if [[ -f "/proc/sys/net/ipv6/conf/${AP_INTERFACE}/disable_ipv6" ]]; then
    echo 1 >"/proc/sys/net/ipv6/conf/${AP_INTERFACE}/disable_ipv6" 2>/dev/null || true
  fi
}

ensure_ap_firewall() {
  # Docker en la Pi suele insertar reglas iptables que bloquean DHCP en uap0.
  if ! command -v iptables >/dev/null 2>&1; then
    return 0
  fi
  iptables -C INPUT -i "${AP_INTERFACE}" -j ACCEPT 2>/dev/null \
    || iptables -I INPUT -i "${AP_INTERFACE}" -j ACCEPT
  iptables -C INPUT -i "${AP_INTERFACE}" -p udp --dport 67 -j ACCEPT 2>/dev/null \
    || iptables -I INPUT -i "${AP_INTERFACE}" -p udp --dport 67 -j ACCEPT
  iptables -C INPUT -i "${AP_INTERFACE}" -p udp --sport 68 -j ACCEPT 2>/dev/null \
    || iptables -I INPUT -i "${AP_INTERFACE}" -p udp --sport 68 -j ACCEPT
  if iptables -L DOCKER-USER >/dev/null 2>&1; then
    iptables -C DOCKER-USER -i "${AP_INTERFACE}" -j ACCEPT 2>/dev/null \
      || iptables -I DOCKER-USER -i "${AP_INTERFACE}" -j ACCEPT
    iptables -C DOCKER-USER -o "${AP_INTERFACE}" -j ACCEPT 2>/dev/null \
      || iptables -I DOCKER-USER -o "${AP_INTERFACE}" -j ACCEPT
  fi
}

prepare_ap_link() {
  reset_ap_link
  assign_ap_ip
}

# Config mínima probada en brcmfmac (Pi Zero 2 W). País vía iw reg, no en hostapd.conf.
write_hostapd_conf() {
  local ssid="$1"
  local channel="$2"
  local password="$3"
  local ieee11n="${4:-0}"
  local conf="${CONFIG_DIR}/hostapd.conf"

  if [[ -z "${password}" || ${#password} -lt 8 || ${#password} -gt 63 ]]; then
    log_error "Contraseña WPA inválida (8-63 chars). Ejecuta: sudo ./scripts/update.sh"
    return 1
  fi
  if [[ "${password}" == *"#"* || "${password}" == *$'\n'* || "${password}" == *$'\r'* ]]; then
    log_error "La contraseña no puede contener '#' ni saltos de línea (limitación hostapd)"
    return 1
  fi

  mkdir -p "${CONFIG_DIR}" "${LOG_DIR}" "${RUN_DIR}"
  python3 - "${conf}" "${AP_INTERFACE}" "${ssid}" "${channel}" "${password}" "${ieee11n}" <<'PY'
import sys
from pathlib import Path

path, interface, ssid, channel, password, ieee11n = sys.argv[1:7]
lines = [
    f"interface={interface}",
    "driver=nl80211",
    "ctrl_interface=/run/nilocardmed/wifi-ap/hostapd-ctrl",
    "ctrl_interface_group=0",
    f"ssid={ssid}",
    "hw_mode=g",
    f"channel={channel}",
    "macaddr_acl=0",
    "ap_isolate=0",
    "auth_algs=1",
    "ignore_broadcast_ssid=0",
    "wpa=2",
    "wpa_key_mgmt=WPA-PSK",
    f"wpa_passphrase={password}",
    "rsn_pairwise=CCMP",
]
if ieee11n == "1":
    lines.extend(["ieee80211n=1", "wmm_enabled=1"])
else:
    lines.extend(["ieee80211n=0", "wmm_enabled=0"])
Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
  log "hostapd.conf escrito (canal ${channel}, ieee80211n=${ieee11n})"
}

write_dnsmasq_conf() {
  local mode="${1:-bind-interfaces}"
  local conf="${CONFIG_DIR}/dnsmasq.conf"

  if [[ "${mode}" == "bind-dynamic" ]]; then
    cat >"${conf}" <<EOF
interface=${AP_INTERFACE}
bind-dynamic
listen-address=${AP_IP}
port=0
dhcp-authoritative
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,12h
dhcp-option=3,${AP_IP}
dhcp-option=6,${AP_IP}
no-hosts
no-resolv
EOF
  else
    cat >"${conf}" <<EOF
interface=${AP_INTERFACE}
bind-interfaces
except-interface=lo
port=0
dhcp-authoritative
dhcp-broadcast
log-dhcp
dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,12h
dhcp-option=3,${AP_IP}
dhcp-option=6,${AP_IP}
no-hosts
no-resolv
EOF
  fi
}

dnsmasq_is_running() {
  [[ -f "${RUN_DIR}/dnsmasq.pid" ]] && kill -0 "$(cat "${RUN_DIR}/dnsmasq.pid")" 2>/dev/null
}

dnsmasq_dhcp_listening() {
  ss -ulnp 2>/dev/null | grep ':67' | grep -qi 'dnsmasq' && return 0
  ss -ulnp 2>/dev/null | grep -q "${AP_IP}:67" && return 0
  ss -ulnp 2>/dev/null | grep -q ':67' && return 0
  return 1
}

log_dnsmasq() {
  mkdir -p "${LOG_DIR}"
  echo "[$(date -Is)] $*" >>"${LOG_DIR}/dnsmasq-start.log"
}

free_dhcp_port() {
  if command -v fuser >/dev/null 2>&1; then
    fuser -k 67/udp 2>/dev/null || true
  fi
}

test_dnsmasq_conf() {
  local conf="${CONFIG_DIR}/dnsmasq.conf"
  dnsmasq --test -C "${conf}" >>"${LOG_DIR}/dnsmasq-start.log" 2>&1
}

verify_ap_active() {
  local attempt ssid=""
  for attempt in $(seq 1 15); do
    if hostapd_is_running; then
      ssid="$(iw dev "${AP_INTERFACE}" info 2>/dev/null | awk -F: '/ssid/ {print $2; exit}' | sed 's/^[[:space:]]*//')"
      if [[ -n "${ssid}" ]]; then
        log "AP activo: SSID=${ssid}"
        return 0
      fi
    fi
    sleep 1
  done
  if hostapd_is_running; then
    log_warn "hostapd corre pero uap0 no emite SSID"
  else
    log_warn "hostapd terminó: $(tail -8 "${LOG_DIR}/hostapd.log" 2>/dev/null | tr '\n' ' ')"
  fi
  return 1
}

stop_dnsmasq() {
  if [[ -f "${RUN_DIR}/dnsmasq.pid" ]]; then
    kill "$(cat "${RUN_DIR}/dnsmasq.pid")" 2>/dev/null || true
    rm -f "${RUN_DIR}/dnsmasq.pid"
  fi
  pkill -f "dnsmasq -C ${CONFIG_DIR}/dnsmasq.conf" 2>/dev/null || true
  pkill -f "dnsmasq.*interface=${AP_INTERFACE}" 2>/dev/null || true
  pkill -f "dnsmasq.*${AP_INTERFACE}" 2>/dev/null || true
}

start_dnsmasq_cli_fallback() {
  log_dnsmasq "fallback CLI en ${AP_INTERFACE} (${AP_IP})"
  dnsmasq \
    --interface="${AP_INTERFACE}" \
    --bind-interfaces \
    --except-interface=lo \
    -p 0 \
    --dhcp-authoritative \
    --dhcp-broadcast \
    --log-dhcp \
    --dhcp-range=192.168.4.10,192.168.4.50,255.255.255.0,12h \
    --dhcp-option=3,"${AP_IP}" \
    --dhcp-option=6,"${AP_IP}" \
    -x "${RUN_DIR}/dnsmasq.pid" \
    >>"${LOG_DIR}/dnsmasq-start.log" 2>&1
}

start_dnsmasq_once() {
  local conf="${CONFIG_DIR}/dnsmasq.conf"
  mkdir -p "${LOG_DIR}" "${RUN_DIR}" "${CONFIG_DIR}"
  log_dnsmasq "=== intento dnsmasq -C ${conf} ==="
  log_dnsmasq "dnsmasq $(dnsmasq --version 2>/dev/null | head -1 || echo '?')"

  if ! command -v dnsmasq >/dev/null 2>&1; then
    log_error "dnsmasq no instalado — sudo apt install dnsmasq"
    return 1
  fi

  if ! test_dnsmasq_conf; then
    log_warn "dnsmasq --test falló: $(tail -3 "${LOG_DIR}/dnsmasq-start.log" | tr '\n' ' ')"
    return 1
  fi

  free_dhcp_port

  if ! dnsmasq -C "${conf}" -x "${RUN_DIR}/dnsmasq.pid" --log-dhcp \
    >>"${LOG_DIR}/dnsmasq-start.log" 2>&1; then
    log_dnsmasq "dnsmasq exit code $?"
    return 1
  fi

  sleep 2
  if dnsmasq_is_running && dnsmasq_dhcp_listening; then
    return 0
  fi
  if dnsmasq_is_running; then
    log_warn "dnsmasq pid activo; comprobando puerto 67..."
    ss -ulnp >>"${LOG_DIR}/dnsmasq-start.log" 2>&1 || true
    dnsmasq_dhcp_listening && return 0
  fi
  log_dnsmasq "falló tras arrancar"
  return 1
}

start_dnsmasq() {
  local mode

  if ! command -v dnsmasq >/dev/null 2>&1; then
    log_error "Paquete dnsmasq no instalado. En la Pi: sudo apt install dnsmasq"
    return 1
  fi

  stop_dnsmasq
  systemctl stop dnsmasq.service 2>/dev/null || true
  systemctl disable dnsmasq.service 2>/dev/null || true
  assign_ap_ip
  ensure_ap_firewall

  if ! ip link show "${AP_INTERFACE}" 2>/dev/null | grep -q "UP"; then
    log_error "${AP_INTERFACE} no está UP — no se puede arrancar dnsmasq"
    return 1
  fi

  for mode in bind-interfaces bind-dynamic; do
    log "Arrancando dnsmasq (${mode}) en ${AP_IP}..."
    write_dnsmasq_conf "${mode}"
    if start_dnsmasq_once; then
      log "dnsmasq activo — DHCP en ${AP_IP}:67"
      return 0
    fi
    stop_dnsmasq
  done

  log_warn "dnsmasq conf falló — probando arranque directo por CLI..."
  free_dhcp_port
  if start_dnsmasq_cli_fallback && sleep 2 && dnsmasq_is_running && dnsmasq_dhcp_listening; then
    log "dnsmasq activo (fallback CLI) — DHCP en ${AP_IP}:67"
    return 0
  fi
  stop_dnsmasq

  log_error "dnsmasq no arrancó — log: ${LOG_DIR}/dnsmasq-start.log"
  tail -15 "${LOG_DIR}/dnsmasq-start.log" >&2 2>/dev/null || true
  log_error "Puerto 67: $(ss -ulnp 2>/dev/null | grep ':67' || echo 'libre/nada')"
  log_error "Procesos: $(pgrep -a dnsmasq 2>/dev/null || echo 'ninguno')"
  return 1
}

stop_udhcpd() {
  if [[ -f "${RUN_DIR}/udhcpd.pid" ]]; then
    kill "$(cat "${RUN_DIR}/udhcpd.pid")" 2>/dev/null || true
    rm -f "${RUN_DIR}/udhcpd.pid"
  fi
  pkill -f "${CONFIG_DIR}/udhcpd.conf" 2>/dev/null || true
}

udhcpd_is_running() {
  [[ -f "${RUN_DIR}/udhcpd.pid" ]] && kill -0 "$(cat "${RUN_DIR}/udhcpd.pid")" 2>/dev/null
}

start_udhcpd() {
  local conf="${CONFIG_DIR}/udhcpd.conf"

  stop_udhcpd
  if ! command -v udhcpd >/dev/null 2>&1; then
    log_warn "udhcpd no instalado — sudo apt install udhcpd"
    return 1
  fi

  mkdir -p "${LOG_DIR}" "${RUN_DIR}" "${CONFIG_DIR}"
  cat >"${conf}" <<EOF
start 192.168.4.10
end 192.168.4.50
interface ${AP_INTERFACE}
max_leases 50
remaining yes
opt dns ${AP_IP}
opt subnet 255.255.255.0
opt router ${AP_IP}
opt lease 432000
EOF

  free_dhcp_port
  systemctl stop udhcpd.service 2>/dev/null || true
  log_dnsmasq "arrancando udhcpd en ${AP_INTERFACE}"
  if ! udhcpd -S "${conf}" >>"${LOG_DIR}/udhcpd.log" 2>&1; then
    log_warn "udhcpd exit code $?"
    return 1
  fi
  sleep 2

  local pid=""
  pid="$(pgrep -xo udhcpd 2>/dev/null || true)"
  if [[ -n "${pid}" ]]; then
    echo "${pid}" >"${RUN_DIR}/udhcpd.pid"
  fi

  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null && dnsmasq_dhcp_listening; then
    log "udhcpd activo — DHCP en ${AP_IP}:67"
    return 0
  fi
  log_warn "udhcpd falló: $(tail -5 "${LOG_DIR}/udhcpd.log" 2>/dev/null | tr '\n' ' ')"
  stop_udhcpd
  return 1
}

start_dhcp_server() {
  assign_ap_ip
  ensure_ap_firewall
  stop_udhcpd

  if start_dnsmasq; then
    return 0
  fi

  log_warn "dnsmasq falló — probando udhcpd (alternativa DHCP en Pi)..."
  stop_dnsmasq
  if start_udhcpd; then
    return 0
  fi

  log_error "Ningún servidor DHCP arrancó (dnsmasq ni udhcpd)"
  return 1
}

stop_dhcp_server() {
  stop_dnsmasq
  stop_udhcpd
}

dhcp_server_running() {
  dnsmasq_is_running || udhcpd_is_running
}

start_hostapd_cli_monitor() {
  local action_script="${INSTALL_DIR}/scripts/wifi-ap-hostapd-action.sh"

  if ! command -v hostapd_cli >/dev/null 2>&1; then
    return 0
  fi
  if [[ ! -f "${action_script}" ]]; then
    log_warn "No encontrado: ${action_script}"
    return 0
  fi

  mkdir -p "${RUN_DIR}/hostapd-ctrl"
  chmod +x "${action_script}"
  pkill -f "hostapd_cli -i ${AP_INTERFACE} -a" 2>/dev/null || true
  hostapd_cli -i "${AP_INTERFACE}" -a "${action_script}" >>"${LOG_DIR}/hostapd-cli.log" 2>&1 &
  echo $! >"${RUN_DIR}/hostapd-cli.pid"
  log "hostapd_cli monitor activo (DHCP al conectar cliente)"
}

stop_hostapd_cli_monitor() {
  if [[ -f "${RUN_DIR}/hostapd-cli.pid" ]]; then
    kill "$(cat "${RUN_DIR}/hostapd-cli.pid")" 2>/dev/null || true
    rm -f "${RUN_DIR}/hostapd-cli.pid"
  fi
  pkill -f "hostapd_cli -i ${AP_INTERFACE} -a" 2>/dev/null || true
}

on_sta_connected() {
  local mac="${1:-}"
  load_deploy_env
  mkdir -p "${LOG_DIR}"
  log "Cliente asociado${mac:+ ${mac}} — uap0 puede pasar a LOWER_UP; arrancando DHCP..."
  sleep 2
  if start_dhcp_server; then
    log "DHCP activo tras conexión de cliente"
    ip -br link show "${AP_INTERFACE}" 2>/dev/null || true
    return 0
  fi
  log_error "DHCP falló tras conexión de cliente — revisa ${LOG_DIR}/dnsmasq-start.log y udhcpd.log"
  return 1
}

repair_dhcp() {
  load_deploy_env
  mkdir -p "${LOG_DIR}" "${RUN_DIR}" "${CONFIG_DIR}"
  log "=== Reparar DHCP (hostapd puede seguir activo) ==="
  if ! ip link show "${AP_INTERFACE}" >/dev/null 2>&1; then
    log_error "No existe ${AP_INTERFACE} — arranca antes: sudo systemctl start nilocardmed-wifi-ap"
    return 1
  fi
  assign_ap_ip
  ensure_ap_firewall
  if start_dhcp_server; then
    log "DHCP reparado. Conecta la tablet y mira: sudo tail -f ${LOG_DIR}/dnsmasq-start.log"
    return 0
  fi
  return 1
}

stop_hostapd() {
  if [[ -f "${RUN_DIR}/hostapd.pid" ]]; then
    kill "$(cat "${RUN_DIR}/hostapd.pid")" 2>/dev/null || true
    rm -f "${RUN_DIR}/hostapd.pid"
  fi
  pkill -f "${CONFIG_DIR}/hostapd.conf" 2>/dev/null || true
}

# hostapd -t falla en brcmfmac con uap0; probamos arranque real con reintentos.
start_hostapd_with_fallback() {
  local ssid="$1"
  local channel="$2"
  local password="$3"
  local ieee11n conf="${CONFIG_DIR}/hostapd.conf"

  prepare_ap_link
  ensure_ap_firewall

  for ieee11n in 0 1; do
    stop_hostapd
    write_hostapd_conf "${ssid}" "${channel}" "${password}" "${ieee11n}"
    log "Arrancando hostapd (ieee80211n=${ieee11n}, canal ${channel}, ${AP_INTERFACE})..."
    if hostapd -B -P "${RUN_DIR}/hostapd.pid" "${conf}" >>"${LOG_DIR}/hostapd.log" 2>&1 \
      && verify_ap_active; then
      assign_ap_ip
      return 0
    fi
    log_warn "hostapd ieee80211n=${ieee11n} falló: $(tail -6 "${LOG_DIR}/hostapd.log" 2>/dev/null | tr '\n' ' ')"
    stop_hostapd
    prepare_ap_link
  done

  log_error "hostapd no pudo inicializar ${AP_INTERFACE}"
  log_error "Log: ${LOG_DIR}/hostapd.log"
  log_error "Prueba manual: sudo iw dev wlan0 interface add uap0 type __ap && sudo hostapd -dd ${conf}"
  return 1
}

prepare_ap_core() {
  load_deploy_env
  mkdir -p "${RUN_DIR}" "${LOG_DIR}" "${CONFIG_DIR}"

  local ap_password ssid channel suffix
  ap_password="$(resolve_ap_password || true)"
  if [[ -z "${ap_password}" ]]; then
    log_error "Sin contraseña WPA en ${INSTALL_DIR}/.env (NILOCARDMED_CONNECTION_PASSWORD)"
    log_error "Ejecuta: sudo ./scripts/update.sh  (≥8 caracteres)"
    return 1
  fi
  if [[ ${#ap_password} -lt 8 || ${#ap_password} -gt 63 ]]; then
    log_error "Contraseña WPA inválida (${#ap_password} chars; necesita 8-63)"
    return 1
  fi

  wait_for_sta || true
  wait_for_sta_channel || true
  ensure_wifi_country

  suffix="$(mac_suffix)"
  ssid="${AP_SSID_PREFIX}-${suffix}"
  channel="$(detect_channel)"

  AP_SSID="${ssid}"
  AP_CHANNEL="${channel}"
  AP_PASSWORD="${ap_password}"

  ensure_ap_interface || return 1
  write_dnsmasq_conf

  log "AP listo para arrancar: SSID=${ssid} canal=${channel} (${AP_INTERFACE})"
  return 0
}

start_ap_services() {
  start_hostapd_with_fallback "${AP_SSID}" "${AP_CHANNEL}" "${AP_PASSWORD}" || return 1
  mkdir -p "${RUN_DIR}/hostapd-ctrl"
  start_hostapd_cli_monitor
  if ! start_dhcp_server; then
    log_warn "DHCP no arrancó al inicio — se reintentará cuando un cliente se conecte (hostapd_cli)"
  fi
  return 0
}

prepare_ap() {
  prepare_ap_core || return 1
  start_ap_services || return 1
  log "AP preparado (hostapd + dnsmasq activos)"
  return 0
}

monitor_hostapd_foreground() {
  local hp_pid="$1"
  trap 'stop_dhcp_server; stop_hostapd_cli_monitor; stop_hostapd; exit 143' TERM INT
  while kill -0 "${hp_pid}" 2>/dev/null; do
    if ! dhcp_server_running || ! dnsmasq_dhcp_listening; then
      log_warn "DHCP caído — reintentando..."
      start_dhcp_server || log_error "No se pudo reiniciar DHCP"
    fi
    sleep 5
  done
  log_error "hostapd (pid ${hp_pid}) terminó — revisa ${LOG_DIR}/hostapd.log"
  stop_dhcp_server
  stop_hostapd_cli_monitor
  return 1
}

run_ap_foreground() {
  local hp_pid=""

  prepare_ap_core || exit 1
  start_ap_services || exit 1

  hp_pid="$(cat "${RUN_DIR}/hostapd.pid")"
  log "Supervisando hostapd pid ${hp_pid} (systemd reinicia si cae)"
  monitor_hostapd_foreground "${hp_pid}"
  exit 1
}

start_ap() {
  stop_hostapd_cli_monitor
  stop_hostapd
  stop_dhcp_server
  prepare_ap || exit 1
  log "AP activo (background)"
}

stop_ap() {
  load_deploy_env
  stop_dhcp_server
  stop_hostapd_cli_monitor
  stop_hostapd

  if ip link show "${AP_INTERFACE}" >/dev/null 2>&1; then
    reset_ap_link
    iw dev "${AP_INTERFACE}" del 2>/dev/null || true
  fi
  log "AP detenido"
}

status_ap() {
  load_deploy_env
  ip -br link show "${AP_INTERFACE}" 2>/dev/null || echo "${AP_INTERFACE}: no existe"
  ip -br addr show "${AP_INTERFACE}" 2>/dev/null || true
  if ip link show "${AP_INTERFACE}" 2>/dev/null | grep -q "LOWER_UP"; then
    echo "uap0: LOWER_UP (cliente asociado o AP activo)"
  else
    echo "uap0: NO-CARRIER (normal sin clientes; tras conectar debe aparecer LOWER_UP)"
  fi
  iw dev "${AP_INTERFACE}" info 2>/dev/null || echo "iw: sin info AP (hostapd probablemente caído)"
  if hostapd_is_running; then
    echo "hostapd: activo pid $(cat "${RUN_DIR}/hostapd.pid")"
  else
    echo "hostapd: NO ACTIVO"
  fi
  if dnsmasq_is_running; then
    echo "dhcp: dnsmasq pid $(cat "${RUN_DIR}/dnsmasq.pid")"
  elif udhcpd_is_running; then
    echo "dhcp: udhcpd pid $(cat "${RUN_DIR}/udhcpd.pid")"
  else
    echo "dhcp: NO ACTIVO (tablet se queda en 'obteniendo IP')"
  fi
  if dnsmasq_dhcp_listening; then
    echo "dhcp: escuchando UDP 67"
  else
    echo "dhcp: NO escucha UDP 67"
  fi
  [[ -f "${LOG_DIR}/dnsmasq-start.log" ]] && echo "--- dnsmasq-start.log ---" \
    && tail -5 "${LOG_DIR}/dnsmasq-start.log" || true
  [[ -f "${LOG_DIR}/udhcpd.log" ]] && echo "--- udhcpd.log ---" \
    && tail -5 "${LOG_DIR}/udhcpd.log" || true
  [[ -f "${LOG_DIR}/sta-connected.log" ]] && echo "--- sta-connected.log ---" \
    && tail -5 "${LOG_DIR}/sta-connected.log" || true
}

diagnose_ap() {
  load_deploy_env
  log "=== Diagnóstico WiFi AP ==="
  log "INSTALL_DIR=${INSTALL_DIR}"
  log "País WiFi: $(iw reg get 2>/dev/null | head -3 | tr '\n' ' ')"
  log "Contraseña WPA: $(resolve_ap_password >/dev/null && echo 'OK (configurada)' || echo 'FALTA')"
  ip -br link show "${STA_INTERFACE}" 2>/dev/null || log_warn "Sin ${STA_INTERFACE}"
  iw dev "${STA_INTERFACE}" link 2>/dev/null | head -5 || log_warn "wlan0 sin enlace"
  log "Canal detectado: $(detect_channel 2>/dev/null || echo '?')"
  ip -br link show "${AP_INTERFACE}" 2>/dev/null || log_warn "Sin ${AP_INTERFACE}"
  iw dev 2>/dev/null || true
  iw list 2>/dev/null | grep -A6 "valid interface combinations" || true
  systemctl status nilocardmed-wifi-ap --no-pager 2>/dev/null || true
  status_ap
  [[ -f "${LOG_DIR}/hostapd.log" ]] && tail -20 "${LOG_DIR}/hostapd.log" || log_warn "Sin ${LOG_DIR}/hostapd.log"
  [[ -f "${LOG_DIR}/dnsmasq-start.log" ]] && tail -15 "${LOG_DIR}/dnsmasq-start.log" || log_warn "Sin ${LOG_DIR}/dnsmasq-start.log"
  ss -ulnp 2>/dev/null | grep -E ':67|:53' || log_warn "Sin dnsmasq en UDP 67 (DHCP)"
  pgrep -a dnsmasq 2>/dev/null || log_warn "Sin proceso dnsmasq"
  log "Si la tablet pide IP pero falla, conéctala y ejecuta en otra terminal:"
  log "  sudo tail -f ${LOG_DIR}/dnsmasq-start.log"
  log "Debes ver DHCPDISCOVER / DHCPOFFER / DHCPACK al conectar."
  [[ -f "${LOG_DIR}/iw-add.err" ]] && cat "${LOG_DIR}/iw-add.err" || true
}

cmd="${1:-start}"
case "${cmd}" in
  start) start_ap ;;
  run) run_ap_foreground ;;
  prepare) prepare_ap_core ;;
  stop) stop_ap ;;
  restart) stop_ap; start_ap ;;
  repair-dhcp) repair_dhcp ;;
  on-sta-connected) on_sta_connected "${2:-}" ;;
  status) status_ap ;;
  diagnose) diagnose_ap ;;
  *)
    echo "Uso: $0 {start|run|prepare|stop|restart|repair-dhcp|status|diagnose}" >&2
    exit 1
    ;;
esac
