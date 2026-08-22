#!/usr/bin/env bash
# Provisiona AP WiFi concurrente (uap0) + systemd en el host Raspberry Pi.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"

# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

if [[ "${EUID}" -ne 0 ]]; then
  log_error "Ejecuta como root (sudo)."
  exit 1
fi

if [[ -f "${INSTALL_DIR}/deploy.env" ]]; then
  # shellcheck disable=SC1091
  set -a && source "${INSTALL_DIR}/deploy.env" && set +a
fi

if ! resolve_wifi_ap_enabled; then
  log_info "ENABLE_WIFI_AP=false — omitiendo configuración de AP"
  exit 0
fi

ensure_wifi_ap_packages

require_command iw
require_command hostapd
require_command dnsmasq
require_command ip

log_info "Configurando AP WiFi concurrente (${WIFI_AP_INTERFACE:-uap0}, ${WIFI_AP_IP:-192.168.4.1})"

mkdir -p /etc/nilocardmed/wifi-ap
mkdir -p /run/nilocardmed/wifi-ap

# NetworkManager no debe gestionar la interfaz virtual del AP.
nm_conf="/etc/NetworkManager/conf.d/nilocardmed-uap0.conf"
if [[ ! -f "${nm_conf}" ]]; then
  cat >"${nm_conf}" <<'EOF'
[keyfile]
unmanaged-devices=interface-name:uap0
EOF
  log_info "NetworkManager: uap0 marcada como unmanaged"
  systemctl reload NetworkManager 2>/dev/null || true
fi

# Evitar que el dnsmasq del sistema choque con el nuestro.
if systemctl is-enabled --quiet dnsmasq.service 2>/dev/null; then
  systemctl disable dnsmasq.service 2>/dev/null || true
  systemctl stop dnsmasq.service 2>/dev/null || true
  log_info "dnsmasq del sistema deshabilitado (usamos instancia dedicada nilocardmed)"
fi

if systemctl is-enabled --quiet hostapd.service 2>/dev/null; then
  systemctl disable hostapd.service 2>/dev/null || true
  systemctl stop hostapd.service 2>/dev/null || true
  log_info "hostapd del sistema deshabilitado (usamos instancia dedicada nilocardmed)"
fi

unit_path="/etc/systemd/system/nilocardmed-wifi-ap.service"
cat >"${unit_path}" <<EOF
[Unit]
Description=NiloCardmed WiFi AP concurrente (uap0)
Documentation=file://${INSTALL_DIR}/docs/Integracion_Frontend_WiFi.md
After=network-online.target NetworkManager.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=INSTALL_DIR=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/scripts/wifi-ap-run.sh start
ExecStop=${INSTALL_DIR}/scripts/wifi-ap-run.sh stop
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
EOF

chmod +x "${INSTALL_DIR}/scripts/wifi-ap-run.sh"
systemctl daemon-reload
systemctl enable nilocardmed-wifi-ap.service
run_with_timeout 120 systemctl restart nilocardmed-wifi-ap.service \
  || log_warn "El AP puede tardar hasta que wlan0 esté listo; reintenta: systemctl restart nilocardmed-wifi-ap"

log_info "Servicio nilocardmed-wifi-ap habilitado"
