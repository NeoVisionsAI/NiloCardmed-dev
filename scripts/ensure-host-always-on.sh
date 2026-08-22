#!/usr/bin/env bash
# Evita suspender/hibernar/apagar pantalla en la Pi (operación 24/7).
# Requiere root. Idempotente — seguro ejecutar en cada install/update.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

write_logind_dropin() {
  local dropin_dir="/etc/systemd/logind.conf.d"
  local dropin_file="${dropin_dir}/nilocardmed-always-on.conf"
  mkdir -p "${dropin_dir}"
  cat >"${dropin_file}" <<'EOF'
[Login]
IdleAction=ignore
IdleActionUSec=0
HandleSuspendKey=ignore
HandleHibernateKey=ignore
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
EOF
  log_info "systemd-logind: ${dropin_file} (sin suspender por inactividad)"
}

mask_sleep_targets() {
  local units=(sleep.target suspend.target hibernate.target hybrid-sleep.target)
  local unit
  for unit in "${units[@]}"; do
    if systemctl mask "${unit}" >/dev/null 2>&1; then
      log_info "systemd: ${unit} enmascarado"
    fi
  done
}

disable_console_blank() {
  if [[ -f /etc/kbd/config ]]; then
    if grep -qE '^[[:space:]]*BLANK_TIME=' /etc/kbd/config; then
      sed -i 's/^[[:space:]]*BLANK_TIME=.*/BLANK_TIME=0/' /etc/kbd/config
    else
      echo "BLANK_TIME=0" >>/etc/kbd/config
    fi
    if grep -qE '^[[:space:]]*POWERDOWN_TIME=' /etc/kbd/config; then
      sed -i 's/^[[:space:]]*POWERDOWN_TIME=.*/POWERDOWN_TIME=0/' /etc/kbd/config
    else
      echo "POWERDOWN_TIME=0" >>/etc/kbd/config
    fi
    log_info "Consola: BLANK_TIME=0 en /etc/kbd/config"
  fi

  local profile_d="/etc/profile.d/nilocardmed-console-always-on.sh"
  cat >"${profile_d}" <<'EOF'
# NiloCardmed: evitar blanking de consola TTY
if command -v setterm >/dev/null 2>&1; then
  setterm -blank 0 -powerdown 0 -powersave off >/dev/null 2>&1 || true
fi
EOF
  chmod 644 "${profile_d}"
  log_info "Consola: ${profile_d}"
}

disable_x_dpms() {
  local autostart_dir="/etc/xdg/autostart"
  local desktop_file="${autostart_dir}/nilocardmed-disable-dpms.desktop"
  mkdir -p "${autostart_dir}"
  cat >"${desktop_file}" <<'EOF'
[Desktop Entry]
Type=Application
Name=NiloCardmed disable DPMS
Comment=Evita apagado de pantalla en escritorio
Exec=sh -c 'for d in :0 ${DISPLAY:-:0}; do DISPLAY=$d xset s off -dpms s noblank 2>/dev/null; done; true'
X-GNOME-Autostart-enabled=true
EOF
  log_info "Escritorio X11/Wayland: ${desktop_file}"

  local lxde_autostart="/etc/xdg/lxsession/LXDE-pi/autostart"
  if [[ -f "${lxde_autostart}" ]] && ! grep -q 'xset s off' "${lxde_autostart}" 2>/dev/null; then
    {
      echo ""
      echo "@xset s off"
      echo "@xset -dpms"
      echo "@xset s noblank"
    } >>"${lxde_autostart}"
    log_info "LXDE-pi autostart: xset añadido"
  fi
}

disable_raspi_blanking() {
  if command -v raspi-config >/dev/null 2>&1; then
    if raspi-config nonint do_blanking 1 2>/dev/null; then
      log_info "raspi-config: blanking de pantalla desactivado"
    fi
  fi
}

ensure_kernel_consoleblank() {
  local cmdline_files=(/boot/firmware/cmdline.txt /boot/cmdline.txt)
  local file
  for file in "${cmdline_files[@]}"; do
    [[ -f "${file}" ]] || continue
    if grep -qE '(^| )consoleblank=' "${file}"; then
      sed -i 's/consoleblank=[0-9]*/consoleblank=0/g' "${file}"
    elif grep -q 'root=' "${file}"; then
      sed -i 's/$/ consoleblank=0/' "${file}"
    fi
    log_info "Kernel cmdline: consoleblank=0 en ${file}"
  done
}

main() {
  if [[ "${EUID}" -ne 0 ]]; then
    log_error "Ejecuta como root: sudo ${SCRIPT_DIR}/ensure-host-always-on.sh"
    exit 1
  fi

  log_info "=== Host always-on (sin suspender / blanking) ==="
  write_logind_dropin
  mask_sleep_targets
  disable_console_blank
  disable_x_dpms
  disable_raspi_blanking
  ensure_kernel_consoleblank

  systemctl restart systemd-logind 2>/dev/null || true
  log_info "Host configurado para operación continua (reinicio recomendado tras el primer install)"
}

main "$@"
