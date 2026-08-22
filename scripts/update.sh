#!/usr/bin/env bash
# Actualización en Pi — UN solo comando (código + Bluetooth host + restart systemd).
#
# Uso:
#   sudo ./scripts/update.sh              # solo código + restart (~segundos)
#   sudo ./scripts/update.sh --build      # rebuild si cambió Dockerfile/requirements
#
# Equivalente:
#   sudo ./scripts/pi-start.sh deploy [--build]
#
# NO hace falta ejecutar a mano ensure-bluetooth-powered ni systemctl restart.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BUILD=false
for arg in "$@"; do
  case "${arg}" in
    --build) BUILD=true ;;
    -h | --help)
      cat <<'EOF'
Uso: sudo ./scripts/update.sh [--build]

  (sin flags)   rsync a /opt/nilocardmed, override compose, Bluetooth host, reinicia servicio
  --build       además reconstruye imagen Docker (usa caché; capas pip cacheadas)

Incluye scripts/ensure-bluetooth-powered.sh (BlueZ Experimental, discoverable, alias).
EOF
      exit 0
      ;;
  esac
done

args=(--skip-host-deps)
if [[ "${BUILD}" != true ]]; then
  args+=(--skip-build)
fi

exec "${SCRIPT_DIR}/install.sh" "${args[@]}"
