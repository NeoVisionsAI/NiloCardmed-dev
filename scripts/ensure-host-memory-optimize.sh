#!/usr/bin/env bash
# Optimizaciones opcionales de RAM en Raspberry Pi (deploy.env).
#   DISABLE_GUI=false     → si true: arranque consola (multi-user), sin escritorio
#   OPTIMIZE_GPU_MEM=true → si true: gpu_mem=16 en config.txt del firmware

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

DISABLE_GUI="${DISABLE_GUI:-false}"
OPTIMIZE_GPU_MEM="${OPTIMIZE_GPU_MEM:-true}"
GPU_MEM_MB="${NILOCARDMED_GPU_MEM_MB:-16}"
GPU_MEM_DESKTOP_MB="${NILOCARDMED_GPU_MEM_DESKTOP_MB:-128}"

find_boot_config() {
  local candidate
  for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [[ -f "${candidate}" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
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
    printf '\n# NiloCardmed: optimización memoria GPU\n%s=%s\n' "${key}" "${value}" >>"${file}"
  fi
}

optimize_gpu_mem() {
  local config_file
  if ! config_file="$(find_boot_config)"; then
    log_warn "No se encontró config.txt del firmware (/boot/firmware o /boot)"
    return 0
  fi

  local current=""
  current="$(grep -E '^[[:space:]]*gpu_mem=' "${config_file}" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' ' || true)"
  if [[ "${current}" == "${GPU_MEM_MB}" ]]; then
    log_info "gpu_mem=${GPU_MEM_MB} ya configurado en ${config_file}"
    return 0
  fi

  set_boot_config_kv "${config_file}" "gpu_mem" "${GPU_MEM_MB}"
  log_info "gpu_mem=${GPU_MEM_MB} aplicado en ${config_file} (reinicio recomendado)"
}

restore_gpu_mem_for_desktop() {
  local config_file current=""
  if ! config_file="$(find_boot_config)"; then
    return 0
  fi

  current="$(grep -E '^[[:space:]]*gpu_mem=' "${config_file}" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d ' ' || true)"
  if [[ -z "${current}" ]]; then
    return 0
  fi
  if [[ "${current}" =~ ^[0-9]+$ ]] && (( current <= 32 )); then
    set_boot_config_kv "${config_file}" "gpu_mem" "${GPU_MEM_DESKTOP_MB}"
    log_info "gpu_mem=${current} → ${GPU_MEM_DESKTOP_MB} en ${config_file} (escritorio activo; reinicio recomendado)"
  fi
}

disable_graphical_target() {
  log_info "DISABLE_GUI=true — arranque en consola (multi-user.target)"

  systemctl set-default multi-user.target

  local unit
  for unit in \
    lightdm.service \
    gdm3.service \
    sddm.service \
    graphical.target; do
    if systemctl list-unit-files "${unit}" >/dev/null 2>&1; then
      systemctl stop "${unit}" 2>/dev/null || true
      systemctl disable "${unit}" 2>/dev/null || true
      log_info "Desactivado: ${unit}"
    fi
  done

  if command -v raspi-config >/dev/null 2>&1; then
    # B2 = consola con autologin; B4 = consola sin autologin
    if raspi-config nonint do_boot_behaviour B2 2>/dev/null; then
      log_info "raspi-config: arranque en consola (do_boot_behaviour B2)"
    fi
  fi

  log_info "Escritorio desactivado; tras reinicio solo habrá consola TTY"
}

main() {
  if [[ "${EUID}" -ne 0 ]]; then
    log_error "Ejecuta como root: sudo ${SCRIPT_DIR}/ensure-host-memory-optimize.sh"
    exit 1
  fi

  log_info "=== Optimización memoria host (DISABLE_GUI=${DISABLE_GUI}, OPTIMIZE_GPU_MEM=${OPTIMIZE_GPU_MEM}) ==="

  if is_true "${DISABLE_GUI}"; then
    disable_graphical_target
    if is_true "${OPTIMIZE_GPU_MEM}"; then
      optimize_gpu_mem
    else
      log_info "OPTIMIZE_GPU_MEM=false — omitiendo gpu_mem"
    fi
  else
    log_info "DISABLE_GUI=false — se mantiene el entorno gráfico"
    restore_gpu_mem_for_desktop
    if is_true "${OPTIMIZE_GPU_MEM}"; then
      log_warn "OPTIMIZE_GPU_MEM=true ignorado con escritorio activo (gpu_mem bajo ralentiza el ratón/X11)"
      log_warn "Para liberar RAM sin GUI: DISABLE_GUI=true en deploy.env y reiniciar"
    fi
  fi
}

main "$@"
