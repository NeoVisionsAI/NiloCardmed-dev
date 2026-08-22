#!/usr/bin/env bash
# Recupera visibilidad BLE tras emparejamientos huérfanos o anuncio LE caído.
# Uso: sudo ./scripts/bluetooth-recover.sh [--purge-bonds] [--restart-service]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

PURGE_BONDS=false
RESTART_SERVICE=true

usage() {
  cat <<'EOF'
Uso: sudo bluetooth-recover.sh [opciones]

Elimina emparejamientos clásicos huérfanos, reactiva discoverable/pairable,
opcionalmente reinicia el servicio NiloCardmed (contenedor Docker).

Opciones:
  --purge-bonds       Elimina todos los dispositivos emparejados en BlueZ (recomendado)
  --no-restart        No reinicia systemd/docker tras recuperar Bluetooth
  -h, --help          Esta ayuda

Web Bluetooth no requiere emparejamiento clásico. Si el tablet eliminó el bond
pero la Pi sigue con icono verde, usa --purge-bonds.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge-bonds) PURGE_BONDS=true ;;
    --no-restart) RESTART_SERVICE=false ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      log_error "Opción desconocida: $1"
      usage
      exit 2
      ;;
  esac
  shift
done

if [[ "${EUID}" -ne 0 ]]; then
  log_error "Ejecuta como root: sudo ${SCRIPT_DIR}/bluetooth-recover.sh"
  exit 1
fi

purge_bluez_bonds() {
  if ! command -v bluetoothctl >/dev/null 2>&1; then
    log_warn "bluetoothctl no disponible — omitiendo purge bonds"
    return 0
  fi

  local mac removed=0
  while IFS= read -r mac; do
    [[ -n "${mac}" ]] || continue
    if bluetoothctl remove "${mac}" >/dev/null 2>&1; then
      log_info "Emparejamiento eliminado: ${mac}"
      removed=$((removed + 1))
    fi
  done < <(bluetoothctl devices Paired 2>/dev/null | awk '{print $2}')

  if [[ "${removed}" -eq 0 ]]; then
    log_info "No había dispositivos emparejados en BlueZ"
  else
    log_info "Emparejamientos eliminados: ${removed}"
  fi
}

main() {
  log_info "=== Recuperación Bluetooth BLE ==="

  if [[ "${PURGE_BONDS}" == true ]]; then
    purge_bluez_bonds
  fi

  if [[ -x "${SCRIPT_DIR}/ensure-bluetooth-powered.sh" ]]; then
    bash "${SCRIPT_DIR}/ensure-bluetooth-powered.sh"
  else
    bluetoothctl power on 2>/dev/null || true
    bluetoothctl discoverable on 2>/dev/null || true
    bluetoothctl pairable on 2>/dev/null || true
  fi

  if command -v bluetoothctl >/dev/null 2>&1; then
    log_info "Estado adaptador:"
    bluetoothctl show 2>/dev/null | grep -iE 'Powered|Discoverable|Pairable|Alias|Advertising|ActiveInstances' || true
  fi

  if [[ "${RESTART_SERVICE}" == true ]]; then
    local unit="${SYSTEMD_UNIT_NAME:-nilocardmed}"
    if systemctl is-enabled "${unit}.service" >/dev/null 2>&1; then
      log_info "Reiniciando ${unit}.service (restaura GATT + anuncio LE)…"
      systemctl restart "${unit}.service"
      sleep 3
      if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^nilocardmed$'; then
        log_info "Contenedor nilocardmed activo"
      else
        log_warn "Contenedor nilocardmed no detectado — revisa: systemctl status ${unit}"
      fi
    else
      log_warn "Unit ${unit}.service no encontrado — reinicia Docker manualmente"
    fi
  fi

  log_info "Comprueba en el tablet: requestDevice con namePrefix 'NiloCardmed'"
  log_info "Diagnóstico en contenedor: docker exec nilocardmed python -m nilocardmed.bluetooth.cli diag"
}

main "$@"
