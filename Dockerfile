# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:3.11-slim-bookworm
FROM ${PYTHON_IMAGE} AS runtime

ARG APP_USER=nilocardmed
ARG APP_UID=1000
ARG APP_GID=1000
ARG APP_HOME=/app
ARG DATA_DIR=/data
ARG LOG_DIR=/var/log/nilocardmed

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NILOCARDMED_DATA_DIR=${DATA_DIR} \
    NILOCARDMED_LOG_DIR=${LOG_DIR}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tini \
        v4l-utils \
        fswebcam \
        ffmpeg \
        gosu \
        zlib1g \
        libjpeg62-turbo \
        libpng16-16 \
        libglib2.0-0 \
        libdbus-1-3 \
        libgirepository-1.0-1 \
        gir1.2-glib-2.0 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "${APP_GID}" "${APP_USER}" \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
        --create-home --home-dir "${APP_HOME}" \
        --shell /usr/sbin/nologin "${APP_USER}"

WORKDIR ${APP_HOME}

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY pyproject.toml requirements.txt README.md ./

# Capa cacheada: dependencias pip (lenta en ARM). Solo se reconstruye si cambian requirements/pyproject.
RUN --mount=type=cache,target=/root/.cache/pip \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        python3-dev \
        pkg-config \
        zlib1g-dev \
        libjpeg62-turbo-dev \
        libpng-dev \
        libdbus-1-dev \
        libglib2.0-dev \
        libgirepository1.0-dev \
    && grep -Ev '^(bluezero|PyGObject|#)' requirements.txt > /tmp/requirements-no-ble.txt \
    && pip install -r /tmp/requirements-no-ble.txt \
    && pip install "PyGObject>=3.42,<4" \
    && pip install --no-deps "bluezero>=0.7,<1" \
    && apt-get purge -y --auto-remove \
        gcc \
        python3-dev \
        pkg-config \
        zlib1g-dev \
        libjpeg62-turbo-dev \
        libpng-dev \
        libdbus-1-dev \
        libglib2.0-dev \
        libgirepository1.0-dev \
    && rm -rf /var/lib/apt/lists/*

# Capa rápida: solo código de la app (cambios frecuentes).
COPY nilocardmed ./nilocardmed

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps --no-cache-dir .

RUN chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p "${DATA_DIR}" "${LOG_DIR}" \
    && chown -R "${APP_UID}:${APP_GID}" "${DATA_DIR}" "${LOG_DIR}" "${APP_HOME}"

ENV APP_UID=${APP_UID} \
    APP_GID=${APP_GID}

USER root

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD gosu "${APP_UID}:${APP_GID}" python -m nilocardmed.main health check --exit-code || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "nilocardmed.main"]
