#!/usr/bin/env bash
# Actualización en Pi — UN solo comando (código + WiFi AP + Docker + restart).
#
# Uso (desde el clone en ~/dev/NiloCardmed-dev):
#   sudo ./scripts/update.sh --pull     # git pull + despliegue completo (recomendado)
#   sudo ./scripts/update.sh            # despliegue sin git pull
#   sudo ./scripts/update.sh --build    # además rebuild imagen Docker
#
# Equivalente: sudo ./scripts/pi-start.sh deploy [--build]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BUILD=false
PULL=false
for arg in "$@"; do
  case "${arg}" in
    --build) BUILD=true ;;
    --pull) PULL=true ;;
    -h | --help)
      cat <<'EOF'
Uso: sudo ./scripts/update.sh [--pull] [--build]

  Comando único de despliegue en la Pi (desde ~/dev/NiloCardmed-dev):

  --pull        git pull + copia a /opt/nilocardmed + todo lo demás (RECOMENDADO)
  (sin flags)   solo despliega el código local actual
  --build       además reconstruye imagen Docker

  update.sh hace automáticamente:
    - rsync del repo → /opt/nilocardmed
    - paquetes WiFi AP si faltan: hostapd, dnsmasq, udhcpd, iw
    - contraseña WPA (NILOCARDMED_CONNECTION_PASSWORD, ≥8 chars)
    - systemd nilocardmed-wifi-ap + DHCP (dnsmasq/udhcpd)
    - reinicio contenedor Docker + AP WiFi

  Ejemplos:
    sudo ./scripts/update.sh --pull
    sudo ./scripts/update.sh --pull --build   # primera vez o cambió Dockerfile

  Comprobar AP tras update:
    sudo /opt/nilocardmed/scripts/wifi-ap-run.sh status
    sudo tail -f /var/log/nilocardmed/wifi-ap/dnsmasq-start.log

  Reparar solo DHCP (sin update completo):
    sudo /opt/nilocardmed/scripts/wifi-ap-run.sh repair-dhcp

  Primera instalación en fábrica: sudo ./scripts/install.sh
EOF
      exit 0
      ;;
  esac
done

if [[ "${PULL}" == true ]]; then
  if [[ -d "${REPO_ROOT}/.git" ]]; then
    echo "[nilocardmed] git pull en ${REPO_ROOT}..."
    git -C "${REPO_ROOT}" pull --ff-only
  else
    echo "[nilocardmed][AVISO] --pull omitido (no hay .git en ${REPO_ROOT})" >&2
  fi
fi

args=(--skip-host-deps --skip-host-tuning)
if [[ "${BUILD}" != true ]]; then
  args+=(--skip-build)
fi

exec "${SCRIPT_DIR}/install.sh" "${args[@]}"
