#!/usr/bin/env bash
# Prueba de estrés del muestreo en primer plano (ejecutar en Pi o contenedor).
# Uso: ./scripts/stress-test.sh [ciclos] [intervalo_seg]
set -euo pipefail

CYCLES="${1:-50}"
INTERVAL="${2:-10}"
DATA_DIR="${NILOCARDMED_DATA_DIR:-./data}"

export NILOCARDMED_DATA_DIR="${DATA_DIR}"
export NILOCARDMED_SAMPLING__INTERVAL_SECONDS="${INTERVAL}"
export NILOCARDMED_SAMPLING__MONITOR_START=-1
export NILOCARDMED_SAMPLING__MONITOR_END=-1
export NILOCARDMED_SAMPLING__MAX_CONSECUTIVE_FAILURES=0

echo "Stress muestreo: ${CYCLES} ciclos cada ${INTERVAL}s"
python -m nilocardmed.main sampler run --max-cycles "${CYCLES}"

echo "--- Salud final ---"
python -m nilocardmed.main health check --exit-code || true
