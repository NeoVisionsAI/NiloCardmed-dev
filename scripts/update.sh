#!/usr/bin/env bash
# Actualización rápida en Pi: sincroniza código y reinicia, sin apt ni rebuild Docker.
#
# Uso:
#   sudo ./scripts/update.sh              # solo código + restart (~segundos)
#   sudo ./scripts/update.sh --build      # rebuild si cambió Dockerfile/requirements
#
# Tras cambiar dependencias pip o Dockerfile:
#   sudo ./scripts/update.sh --build
# La primera build sigue tardando ~15 min; las siguientes reutilizan caché de capas.

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
