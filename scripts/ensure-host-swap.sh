#!/usr/bin/env bash
# Asegura al menos 1 GB de swap en /var/swap (Pi Zero 2 W / 512 MB RAM).
# Idempotente — solo actúa si la swap total es inferior al mínimo.
# Ver docs/INCREASE_SWAP.md

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

SWAP_FILE="${NILOCARDMED_SWAP_FILE:-/var/swap}"
DESIRED_SWAP_MB="${NILOCARDMED_SWAP_SIZE_MB:-1024}"
MIN_FREE_DISK_MB=$((DESIRED_SWAP_MB + 256))

current_swap_mb() {
  free -m | awk '/^Swap:/ {print $2; exit}'
}

swap_file_size_mb() {
  if [[ ! -f "${SWAP_FILE}" ]]; then
    echo 0
    return
  fi
  stat -c '%s' "${SWAP_FILE}" 2>/dev/null | awk '{printf "%d", $1/1024/1024}'
}

disable_dphys_swapfile() {
  if ! systemctl list-unit-files dphys-swapfile.service >/dev/null 2>&1; then
    return 0
  fi
  systemctl stop dphys-swapfile.service 2>/dev/null || true
  systemctl disable dphys-swapfile.service 2>/dev/null || true
  log_info "dphys-swapfile desactivado (evita conflicto con ${SWAP_FILE})"
}

ensure_fstab_entry() {
  if grep -qE "[[:space:]]${SWAP_FILE}[[:space:]]" /etc/fstab 2>/dev/null; then
    return 0
  fi
  echo "${SWAP_FILE} none swap sw 0 0" >>/etc/fstab
  log_info "/etc/fstab: entrada añadida para ${SWAP_FILE}"
}

ensure_swapon() {
  if swapon --show 2>/dev/null | grep -qF "${SWAP_FILE}"; then
    return 0
  fi
  swapon "${SWAP_FILE}"
  log_info "Swap activada: ${SWAP_FILE}"
}

create_swap_file() {
  local avail_mb
  avail_mb="$(df -m /var 2>/dev/null | awk 'NR==2 {print $4}' || echo 0)"
  if [[ "${avail_mb}" -lt "${MIN_FREE_DISK_MB}" ]]; then
    log_warn "Espacio insuficiente en /var (${avail_mb} MB libres; se necesitan ~${MIN_FREE_DISK_MB} MB)"
    log_warn "Omitiendo ampliación de swap — libera espacio en la SD e vuelve a ejecutar install/update"
    return 1
  fi

  log_info "Creando ${SWAP_FILE} (${DESIRED_SWAP_MB} MB) — puede tardar un minuto en la SD..."
  disable_dphys_swapfile
  swapoff -a 2>/dev/null || true

  if [[ -f "${SWAP_FILE}" ]]; then
    rm -f "${SWAP_FILE}"
  fi

  if dd if=/dev/zero of="${SWAP_FILE}" bs=1M count="${DESIRED_SWAP_MB}" status=none 2>/dev/null; then
    :
  else
    dd if=/dev/zero of="${SWAP_FILE}" bs=1M count="${DESIRED_SWAP_MB}"
  fi

  chmod 600 "${SWAP_FILE}"
  mkswap "${SWAP_FILE}" >/dev/null
  swapon "${SWAP_FILE}"
  ensure_fstab_entry
  log_info "Swap configurada: ${DESIRED_SWAP_MB} MB en ${SWAP_FILE}"
}

main() {
  if [[ "${EUID}" -ne 0 ]]; then
    log_error "Ejecuta como root: sudo ${SCRIPT_DIR}/ensure-host-swap.sh"
    exit 1
  fi

  local current file_mb
  current="$(current_swap_mb)"
  file_mb="$(swap_file_size_mb)"

  log_info "=== Swap del host (objetivo: ${DESIRED_SWAP_MB} MB) ==="
  log_info "Swap actual: ${current} MB | ${SWAP_FILE}: ${file_mb} MB"

  if [[ "${current}" -ge "${DESIRED_SWAP_MB}" ]] && [[ "${file_mb}" -ge "${DESIRED_SWAP_MB}" ]]; then
    ensure_swapon
    ensure_fstab_entry
    log_info "Swap suficiente (${current} MB); no se requieren cambios"
    return 0
  fi

  if [[ -f "${SWAP_FILE}" ]] && [[ "${file_mb}" -ge "${DESIRED_SWAP_MB}" ]]; then
    disable_dphys_swapfile
    swapoff -a 2>/dev/null || true
    ensure_swapon
    ensure_fstab_entry
    log_info "Swap reactivada desde ${SWAP_FILE} (${file_mb} MB)"
    return 0
  fi

  create_swap_file || return 0

  current="$(current_swap_mb)"
  log_info "Verificación: Swap total ahora ${current} MB (free -h)"
}

main "$@"
