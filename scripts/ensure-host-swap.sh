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
# Objetivo 1024 MB: free -m puede mostrar 1023, 1000… — no recrear por 1–24 MB de diferencia.
SWAP_MIN_REPORTED_MB="${NILOCARDMED_SWAP_MIN_REPORTED_MB:-1000}"
SWAP_MIN_FILE_MB="${NILOCARDMED_SWAP_MIN_FILE_MB:-1000}"
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

fstab_has_swap_entry() {
  grep -qE "^[[:space:]]*${SWAP_FILE}[[:space:]]" /etc/fstab 2>/dev/null
}

dedupe_fstab_swap() {
  [[ -f /etc/fstab ]] || return 0
  local tmp count
  count="$(grep -cE "^[[:space:]]*${SWAP_FILE}[[:space:]]" /etc/fstab 2>/dev/null || echo 0)"
  if [[ "${count}" -le 1 ]]; then
    return 0
  fi
  log_warn "/etc/fstab: ${count} entradas duplicadas de ${SWAP_FILE}; dejando una sola"
  tmp="$(mktemp)"
  awk -v swap="${SWAP_FILE}" '
    $1 == swap { if (seen++) next }
    { print }
  ' /etc/fstab >"${tmp}"
  mv "${tmp}" /etc/fstab
}

ensure_fstab_entry() {
  dedupe_fstab_swap

  if fstab_has_swap_entry; then
    if grep -qE "^[[:space:]]*${SWAP_FILE}[[:space:]]none[[:space:]]swap[[:space:]]sw[[:space:]]0[[:space:]]0" /etc/fstab 2>/dev/null \
      && ! grep -qE "^[[:space:]]*${SWAP_FILE}[[:space:]]none[[:space:]]swap[[:space:]]sw,nofail" /etc/fstab 2>/dev/null; then
      sed -i "s|^[[:space:]]*${SWAP_FILE}[[:space:]]none swap sw 0 0|${SWAP_FILE} none swap sw,nofail 0 0|" /etc/fstab
      log_info "/etc/fstab: swap actualizada con nofail"
    fi
    return 0
  fi
  echo "${SWAP_FILE} none swap sw,nofail 0 0" >>/etc/fstab
  log_info "/etc/fstab: entrada añadida para ${SWAP_FILE} (sw,nofail)"
}

ensure_swapon() {
  if swapon --show 2>/dev/null | grep -qF "${SWAP_FILE}"; then
    return 0
  fi
  if swapon "${SWAP_FILE}" 2>/dev/null; then
    log_info "Swap activada: ${SWAP_FILE}"
  else
    log_warn "No se pudo activar ${SWAP_FILE} (omitido swapoff para no saturar RAM)"
    return 1
  fi
}

swap_file_active() {
  swapon --show 2>/dev/null | grep -qF "${SWAP_FILE}"
}

swap_is_adequate() {
  local current="$1"
  local file_mb="$2"

  # Fichero /var/swap ~1 GB ya creado → no swapoff ni dd aunque free -m diga 1023.
  if [[ -f "${SWAP_FILE}" ]] && [[ "${file_mb}" -ge "${SWAP_MIN_FILE_MB}" ]]; then
    return 0
  fi
  if [[ "${current}" -ge "${SWAP_MIN_REPORTED_MB}" ]] && [[ "${file_mb}" -ge "${SWAP_MIN_FILE_MB}" ]]; then
    return 0
  fi
  return 1
}

swapoff_target_swaps() {
  # Nunca usar swapoff -a en Pi 512 MB con Docker: puede provocar OOM y corrupción ext4.
  if swapon --show 2>/dev/null | grep -qF "${SWAP_FILE}"; then
    swapoff "${SWAP_FILE}" 2>/dev/null || log_warn "No se pudo desactivar ${SWAP_FILE}"
  fi
  local dphys="/var/swap.dphys"
  if [[ -f "${dphys}" ]] && swapon --show 2>/dev/null | grep -qF "${dphys}"; then
    swapoff "${dphys}" 2>/dev/null || true
  fi
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
  swapoff_target_swaps

  if [[ -f "${SWAP_FILE}" ]]; then
    rm -f "${SWAP_FILE}"
  fi

  if ! dd if=/dev/zero of="${SWAP_FILE}" bs=1M count="${DESIRED_SWAP_MB}" conv=fsync status=none 2>/dev/null; then
    dd if=/dev/zero of="${SWAP_FILE}" bs=1M count="${DESIRED_SWAP_MB}" conv=fsync
  fi

  chmod 600 "${SWAP_FILE}"
  if ! mkswap "${SWAP_FILE}" >/dev/null 2>&1; then
    log_error "mkswap falló — no se modifica /etc/fstab (evita bloquear el arranque)"
    rm -f "${SWAP_FILE}"
    return 1
  fi
  if ! swapon "${SWAP_FILE}" 2>/dev/null; then
    log_error "swapon falló — no se modifica /etc/fstab"
    rm -f "${SWAP_FILE}"
    return 1
  fi
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
  dedupe_fstab_swap
  log_info "Swap actual: ${current} MB | ${SWAP_FILE}: ${file_mb} MB"

  if swap_is_adequate "${current}" "${file_mb}"; then
    ensure_fstab_entry
    if ! swap_file_active; then
      ensure_swapon || true
    fi
    log_info "Swap suficiente (${current} MB reportados, ${file_mb} MB en disco); sin swapoff"
    return 0
  fi

  if [[ -f "${SWAP_FILE}" ]] && [[ "${file_mb}" -ge "${SWAP_MIN_FILE_MB}" ]]; then
    disable_dphys_swapfile
    ensure_swapon || true
    ensure_fstab_entry
    log_info "Swap: fichero ${file_mb} MB presente (≥${SWAP_MIN_FILE_MB}); sin swapoff"
    return 0
  fi

  create_swap_file || return 0

  current="$(current_swap_mb)"
  log_info "Verificación: Swap total ahora ${current} MB (free -h)"
}

main "$@"
