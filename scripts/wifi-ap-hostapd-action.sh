#!/usr/bin/env bash
# Callback de hostapd_cli cuando un cliente se asocia al AP (uap0).
set -euo pipefail

EVENT="${1:-}"
MAC="${2:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/nilocardmed}"
RUN_SCRIPT="${INSTALL_DIR}/scripts/wifi-ap-run.sh"

case "${EVENT}" in
  AP-STA-CONNECTED)
    if [[ -x "${RUN_SCRIPT}" ]]; then
      INSTALL_DIR="${INSTALL_DIR}" "${RUN_SCRIPT}" on-sta-connected "${MAC}" >>/var/log/nilocardmed/wifi-ap/sta-connected.log 2>&1 || true
    fi
    ;;
esac
