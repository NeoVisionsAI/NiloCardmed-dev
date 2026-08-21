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
    PIP_NO_CACHE_DIR=1 \
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
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "${APP_GID}" "${APP_USER}" \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
        --create-home --home-dir "${APP_HOME}" \
        --shell /usr/sbin/nologin "${APP_USER}"

WORKDIR ${APP_HOME}

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY pyproject.toml requirements.txt README.md ./
COPY nilocardmed ./nilocardmed

# En ARM (Pi Zero) Pillow suele compilarse desde fuente: hace falta zlib/jpeg dev.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        python3-dev \
        zlib1g-dev \
        libjpeg62-turbo-dev \
        libpng-dev \
    && pip install --no-cache-dir . \
    && apt-get purge -y --auto-remove \
        gcc \
        python3-dev \
        zlib1g-dev \
        libjpeg62-turbo-dev \
        libpng-dev \
    && rm -rf /var/lib/apt/lists/*

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
