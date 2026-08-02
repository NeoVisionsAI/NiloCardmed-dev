#!/usr/bin/env bash
# Comprobación de salud para Docker healthcheck o cron en la Pi.
set -euo pipefail

INSTALL_DIR="${NILOCARDMED_INSTALL_DIR:-/opt/nilocardmed}"
cd "${INSTALL_DIR}"

if [[ -f deploy.env ]]; then
  # shellcheck disable=SC1091
  source deploy.env
fi

COMPOSE=( ${DOCKER_COMPOSE_CMD:-docker compose} )
if [[ -n "${COMPOSE_FILE:-}" ]]; then
  COMPOSE+=( -f ${COMPOSE_FILE//:/ -f } )
fi
if [[ -f docker-compose.override.yml ]]; then
  COMPOSE+=( -f docker-compose.override.yml )
fi

exec "${COMPOSE[@]}" exec -T "${COMPOSE_SERVICE_NAME:-nilocardmed}" \
  python -m nilocardmed.main health check --exit-code
