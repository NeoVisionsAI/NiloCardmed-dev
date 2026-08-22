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

  (sin flags)   rsync a /opt/nilocardmed, contraseña AP, reinicia servicios
  --build       además reconstruye imagen Docker (solo si cambió Dockerfile/deps)

  Tras migración WiFi: sudo ./scripts/update.sh  (pide contraseña; monta código Python)
  Si HTTP sigue sin responder: sudo ./scripts/update.sh --build  (una vez)

  No ejecuta swap/always-on/gpu en caliente (evita colgar SSH/escritorio en Pi Zero 2 W).
  Primera instalación: sudo ./scripts/install.sh (sí aplica tuning de host).

Incluye scripts/ensure-bluetooth-powered.sh (BlueZ Experimental, discoverable, alias).
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
