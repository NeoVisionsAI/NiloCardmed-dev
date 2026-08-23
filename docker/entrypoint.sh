#!/bin/sh
set -e

APP_UID="${APP_UID:-1000}"
APP_GID="${APP_GID:-1000}"
DATA_DIR="${NILOCARDMED_DATA_DIR:-/data}"
LOG_DIR="${NILOCARDMED_LOG_DIR:-/var/log/nilocardmed}"

drop_privileges() {
  # Docker group_add (video, netdev…) solo aplica al PID 1; gosu pierde esos grupos.
  if command -v setpriv >/dev/null 2>&1; then
    exec setpriv --reuid="${APP_UID}" --regid="${APP_GID}" --keep-groups "$@"
  fi
  exec gosu "${APP_UID}:${APP_GID}" "$@"
}

if [ "$(id -u)" = "0" ]; then
  mkdir -p "${DATA_DIR}" "${LOG_DIR}"
  chown -R "${APP_UID}:${APP_GID}" "${DATA_DIR}" "${LOG_DIR}" || true
  drop_privileges /usr/bin/tini -- "$@"
fi

exec /usr/bin/tini -- "$@"
