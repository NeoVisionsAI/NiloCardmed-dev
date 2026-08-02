#!/bin/sh
set -e

APP_UID="${APP_UID:-1000}"
APP_GID="${APP_GID:-1000}"
DATA_DIR="${NILOCARDMED_DATA_DIR:-/data}"
LOG_DIR="${NILOCARDMED_LOG_DIR:-/var/log/nilocardmed}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "${DATA_DIR}" "${LOG_DIR}"
  chown -R "${APP_UID}:${APP_GID}" "${DATA_DIR}" "${LOG_DIR}" || true
  exec gosu "${APP_UID}:${APP_GID}" /usr/bin/tini -- "$@"
fi

exec /usr/bin/tini -- "$@"
