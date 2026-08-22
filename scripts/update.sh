#!/usr/bin/env bash
# Actualización en Pi — UN solo comando (código + WiFi AP + Docker + restart).
#
# Uso:
#   sudo ./scripts/update.sh              # despliega /opt/nilocardmed y reinicia servicios
#   sudo ./scripts/update.sh --build      # además rebuild imagen Docker
#
# Equivalente: sudo ./scripts/pi-start.sh deploy [--build]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BUILD=false
for arg in "$@"; do
  case "${arg}" in
    --build) BUILD=true ;;
    -h | --help)
      cat <<'EOF'
Uso: sudo ./scripts/update.sh [--build]

  Comando único de despliegue en la Pi (desde ~/dev/NiloCardmed-dev):

  (sin flags)   copia a /opt/nilocardmed, permisos, paquetes AP, contraseña WPA, reinicios
  --build       además reconstruye imagen Docker

  update.sh hace automáticamente:
    - rsync del repo → /opt/nilocardmed
    - chmod +x en scripts/
    - paquetes WiFi AP si faltan: hostapd, dnsmasq, udhcpd, iw
    - contraseña WPA (NILOCARDMED_CONNECTION_PASSWORD, ≥8 chars)
    - systemd nilocardmed-wifi-ap + DHCP (dnsmasq/udhcpd)
    - reinicio contenedor Docker + AP WiFi

  Ejemplos:
    sudo ./scripts/update.sh
    sudo ./scripts/update.sh --build

  Comprobar AP:
    sudo /opt/nilocardmed/scripts/wifi-ap-run.sh status

  Reparar solo DHCP:
    sudo /opt/nilocardmed/scripts/wifi-ap-run.sh repair-dhcp

  Primera instalación: sudo ./scripts/install.sh
EOF
      exit 0
      ;;
  esac
done

args=(--skip-host-deps --skip-host-tuning)
if [[ "${BUILD}" != true ]]; then
  args+=(--skip-build)
fi

exec "${SCRIPT_DIR}/install.sh" "${args[@]}"
