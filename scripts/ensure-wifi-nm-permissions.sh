#!/usr/bin/env bash
# Permite que el contenedor (uid APP_UID) use nmcli connect/disconnect vía NetworkManager.
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

if ! is_true "${ENABLE_WIFI:-false}"; then
  log_info "ENABLE_WIFI=false — omitiendo permisos NetworkManager"
  exit 0
fi

if ! command -v iw >/dev/null 2>&1; then
  log_info "Instalando iw (escaneo WiFi en AP+STA)..."
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iw iproute2 || log_warn "No se pudo instalar iw"
fi

APP_UID="${APP_UID:-1000}"
RUN_USER="${NILOCARDMED_RUN_USER:-$(getent passwd "${APP_UID}" | cut -d: -f1 || echo "")}"
WIFI_SCRIPT="${INSTALL_DIR}/scripts/wifi-host.sh"

polkit_dir="/etc/polkit-1/rules.d"
polkit_rule="${polkit_dir}/49-nilocardmed-networkmanager.rules"

mkdir -p "${polkit_dir}"
cat >"${polkit_rule}" <<EOF
// NiloCardmed — WiFi desde contenedor Docker (nmcli vía D-Bus / polkit)
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") == 0) {
        if (subject.uid == ${APP_UID}) {
            return polkit.Result.YES;
        }
    }
});
EOF
chmod 644 "${polkit_rule}"
log_info "Polkit: ${polkit_rule} (uid ${APP_UID})"

if [[ -n "${RUN_USER}" ]] && command -v sudo >/dev/null 2>&1; then
  sudoers_file="/etc/sudoers.d/nilocardmed-wifi"
  cat >"${sudoers_file}" <<EOF
# NiloCardmed — fallback nmcli WiFi (connect/disconnect) desde contenedor
Defaults:${RUN_USER} !requiretty
${RUN_USER} ALL=(root) NOPASSWD: ${WIFI_SCRIPT}
EOF
  chmod 440 "${sudoers_file}"
  if visudo -cf "${sudoers_file}" >/dev/null 2>&1; then
    log_info "Sudoers: ${RUN_USER} → ${WIFI_SCRIPT}"
  else
    log_warn "Sudoers inválido — eliminando ${sudoers_file}"
    rm -f "${sudoers_file}"
  fi
fi

if [[ -n "${RUN_USER}" ]] && getent group netdev >/dev/null 2>&1; then
  usermod -aG netdev "${RUN_USER}" 2>/dev/null || true
  log_info "Usuario ${RUN_USER} en grupo netdev"
fi

log_info "Permisos WiFi NetworkManager aplicados. Reinicia el contenedor: sudo systemctl restart nilocardmed"
