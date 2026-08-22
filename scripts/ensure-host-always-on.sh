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

disable_raspi_screen_blanking() {
  # Equivalente a: raspi-config → Display Options → Screen Blanking → No
  if command -v raspi-config >/dev/null 2>&1; then
    log_info "raspi-config: Display Options > Screen Blanking > No (do_blanking 1)"
    if raspi-config nonint do_blanking 1; then
      log_info "raspi-config: Screen Blanking desactivado"
    else
      log_warn "raspi-config do_blanking no aplicó cambios (continuando con alternativas)"
    fi
  else
    log_warn "raspi-config no instalado — omitiendo do_blanking"
  fi
}

set_boot_config_kv() {
  local file="$1"
  local key="$2"
  local value="$3"

  if grep -qE "^[[:space:]]*${key}=" "${file}"; then
    sed -i "s/^[[:space:]]*${key}=.*/${key}=${value}/" "${file}"
  elif grep -qE "^[[:space:]]*#${key}=" "${file}"; then
    sed -i "s/^[[:space:]]*#${key}=.*/${key}=${value}/" "${file}"
  else
    printf '\n# NiloCardmed: pantalla siempre encendida\n%s=%s\n' "${key}" "${value}" >>"${file}"
  fi
}

disable_hdmi_blanking() {
  local config_file=""
  for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [[ -f "${candidate}" ]]; then
      config_file="${candidate}"
      break
    fi
  done
  if [[ -z "${config_file}" ]]; then
    return 0
  fi
  # hdmi_blanking=0 → no apagar señal HDMI por inactividad
  set_boot_config_kv "${config_file}" "hdmi_blanking" "0"
  log_info "Firmware ${config_file}: hdmi_blanking=0"
}

remove_lightdm_xserver_dropin() {
  # NO usar xserver-command en lightdm: rompe X11 en Raspberry Pi OS (pantalla negra / solo ratón).
  # DPMS se controla con xset en autostart (disable_x_dpms) y raspi-config.
  local dropin_dir="/etc/lightdm/lightdm.conf.d"
  local changed=false
  local path

  [[ -d "${dropin_dir}" ]] || return 0

  shopt -s nullglob
  for path in \
    "${dropin_dir}/nilocardmed-no-blanking.conf" \
    "${dropin_dir}/nilocardmed-no-blanking.conf.bak" \
    "${dropin_dir}"/nilocardmed-no-blanking.conf.*; do
    [[ -f "${path}" ]] || continue
    [[ "${path}" == *.disabled ]] && continue
    mv "${path}" "${path}.disabled"
    log_info "lightdm: dropin obsoleto desactivado: ${path}"
    changed=true
  done
  shopt -u nullglob

  # Cualquier dropin nuestro antiguo con xserver-command=X
  while IFS= read -r -d '' path; do
    if grep -qE '^[[:space:]]*xserver-command=X' "${path}" 2>/dev/null; then
      mv "${path}" "${path}.disabled"
      log_info "lightdm: desactivado ${path} (xserver-command rompe el escritorio)"
      changed=true
    fi
  done < <(find "${dropin_dir}" -maxdepth 1 -type f -name '*.conf' -print0 2>/dev/null)

  if [[ "${changed}" == true ]]; then
    RESTORE_LIGHTDM_AFTER=1
  fi
}

restore_lightdm_session_if_needed() {
  [[ "${RESTORE_LIGHTDM_AFTER:-0}" == 1 ]] || [[ "${NILOCARDMED_RESTORE_DESKTOP_AFTER:-}" == "always" ]] || return 0
  if ! systemctl is-active lightdm >/dev/null 2>&1; then
    return 0
  fi
  log_info "lightdm: reiniciando sesión gráfica (restaurar escritorio tras install/update)…"
  systemctl restart lightdm || log_warn "No se pudo reiniciar lightdm"
}

disable_desktop_power_management() {
  local user=""
  for user in pi "${SUDO_USER:-}"; do
    [[ -n "${user}" && "${user}" != "root" ]] || continue
    id "${user}" >/dev/null 2>&1 || continue
    if ! command -v gsettings >/dev/null 2>&1; then
      continue
    fi
    local uid dbus_addr=""
    uid="$(id -u "${user}")"
    if [[ -S "/run/user/${uid}/bus" ]]; then
      dbus_addr="unix:path=/run/user/${uid}/bus"
    fi
    if sudo -u "${user}" DBUS_SESSION_BUS_ADDRESS="${dbus_addr}" \
      gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null; then
      log_info "gsettings (${user}): idle-delay=0"
    fi
    sudo -u "${user}" DBUS_SESSION_BUS_ADDRESS="${dbus_addr}" \
      gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing' 2>/dev/null || true
    sudo -u "${user}" DBUS_SESSION_BUS_ADDRESS="${dbus_addr}" \
      gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing' 2>/dev/null || true
  done
}

apply_active_tty_blanking_off() {
  if command -v setterm >/dev/null 2>&1; then
    for tty in /dev/tty[0-9]*; do
      [[ -c "${tty}" ]] || continue
      setterm -term linux -blank 0 -powerdown 0 -powersave off >"${tty}" 2>/dev/null || true
    done
    log_info "TTY activos: setterm blank=0 aplicado"
  fi
}

ensure_kernel_consoleblank() {
  local cmdline_files=()
  if [[ -f /boot/firmware/cmdline.txt ]]; then
    cmdline_files=(/boot/firmware/cmdline.txt)
  elif [[ -f /boot/cmdline.txt ]]; then
    cmdline_files=(/boot/cmdline.txt)
  fi

  local file
  for file in "${cmdline_files[@]}"; do
    [[ -f "${file}" ]] || continue
    # cmdline.txt debe ser UNA sola línea; no tocar ambos paths (evita duplicar parámetros).
    if grep -qE '(^| )consoleblank=' "${file}"; then
      sed -i 's/consoleblank=[0-9]*/consoleblank=0/g' "${file}"
    elif grep -q 'root=' "${file}"; then
      sed -i 's/[[:space:]]*$/ consoleblank=0/' "${file}"
    fi
    log_info "Kernel cmdline: consoleblank=0 en ${file}"
  done
}

main() {
  if [[ "${EUID}" -ne 0 ]]; then
    log_error "Ejecuta como root: sudo ${SCRIPT_DIR}/ensure-host-always-on.sh"
    exit 1
  fi

  RESTORE_LIGHTDM_AFTER=0

  if is_true "${NILOCARDMED_HOST_TUNING_LIGHT:-false}"; then
    log_info "=== Host tuning ligero (solo lightdm obsoleto) ==="
    remove_lightdm_xserver_dropin
    restore_lightdm_session_if_needed
    return 0
  fi

  log_info "=== Host always-on (sin suspender / blanking / pantalla negra) ==="
  remove_lightdm_xserver_dropin
  write_logind_dropin
  mask_sleep_targets
  disable_console_blank
  disable_hdmi_blanking
  disable_raspi_screen_blanking
  disable_x_dpms
  disable_desktop_power_management
  ensure_kernel_consoleblank
  apply_active_tty_blanking_off
  remove_lightdm_xserver_dropin
  restore_lightdm_session_if_needed

  # No reiniciar systemd-logind con lightdm activo: tumba la sesión X (pantalla negra / solo ratón).
  log_info "systemd-logind: dropin aplicado (efecto completo tras reboot; no se reinicia en caliente)"
  log_info "Pantalla/consola: sin blanking (equiv. raspi-config Screen Blanking > No)"
  log_info "Reinicio recomendado tras el primer install para hdmi_blanking/consoleblank"
}

main "$@"
