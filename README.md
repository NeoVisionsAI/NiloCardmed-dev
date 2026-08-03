# NiloCardmed-dev

Software embebido para Raspberry Pi Zero W2: captura periódica de imágenes desde cámara USB y envío a SER, con configuración local vía Bluetooth.

Ver [PLANIFICACION.md](PLANIFICACION.md) para la descripción completa y las fases de desarrollo.

## Desarrollo local (Fase 0)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Copiar variables de entorno de ejemplo
cp .env.example .env

# Ejecutar
NILOCARDMED_DATA_DIR=./data python -m nilocardmed.main
```

La configuración persistida se guarda en `data/config.json`. Las variables de entorno con prefijo `NILOCARDMED_` sobreescriben los valores del fichero (ver `.env.example`).

## Docker y despliegue en Raspberry Pi (Fase 1)

Guía paso a paso (recomendada): **[docs/DESPLIEGUE.md](docs/DESPLIEGUE.md)** — incluye checklist, logs y troubleshooting.

### Parametrización

| Fichero | Propósito |
|---------|-----------|
| `.env` | Configuración de la aplicación (SER, muestreo, Bluetooth, etc.) |
| `deploy.env` | Despliegue: Docker, volúmenes, hardware, systemd |
| `docker-compose.yml` | Servicio base |
| `docker-compose.pi.yml` | Cámara USB y grupos en Pi |
| `docker-compose.override.yml` | Generado según flags USB/Bluetooth/privileged |

Copia las plantillas:

```bash
cp .env.example .env
cp deploy.env.example deploy.env
# Edita ambos ficheros según tu entorno
```

### Desarrollo local con Docker

```bash
# x86/amd64 (PC de desarrollo)
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose up --build

# Simular Pi Zero W2 (ARMv7)
DOCKER_DEFAULT_PLATFORM=linux/arm/v7 docker compose -f docker-compose.yml -f docker-compose.pi.yml up --build
```

### Instalación en Raspberry Pi (arranque automático)

Requisitos: Docker Engine + Compose v2, usuario con permisos Docker.

```bash
cp deploy.env.example deploy.env
# Ajusta HOST_DATA_DIR, HOST_LOG_DIR, VIDEO_DEVICE_HOST, APP_UID/GID, etc.

sudo ./scripts/install.sh
```

El script:

1. Carga `deploy.env` (todas las rutas y flags son configurables).
2. Genera `docker-compose.override.yml` (USB bus, Bluetooth, privileged).
3. Construye la imagen para `linux/arm/v7`.
4. Instala y activa la unidad systemd (`nilocardmed.service` por defecto).

Tras un reinicio, systemd levanta el stack y Docker aplica `restart: unless-stopped`.

```bash
# Estado
sudo systemctl status nilocardmed
docker compose ps

# Logs
journalctl -u nilocardmed -f
docker compose logs -f

# Desinstalar servicio (conserva datos)
sudo ./scripts/uninstall.sh
```

Variables de despliegue destacadas (`deploy.env`):

- `RESTART_POLICY` — política de reinicio del contenedor
- `HOST_DATA_DIR` / `HOST_LOG_DIR` — persistencia en host
- `VIDEO_DEVICE_HOST` — dispositivo de cámara
- `MOUNT_USB_BUS` — acceso al bus USB
- `ENABLE_BLUETOOTH` / `BLUETOOTH_DBUS_SYSTEM_PATH` — preparado para Fase 6
- `NILOCARDMED_INSTALL_DIR` — ruta de instalación en producción

## Cámara USB (Fase 2)

Módulo `nilocardmed/camera/` con detección V4L2 y captura JPEG parametrizable.

### Configuración (`NILOCARDMED_CAMERA__*`)

| Variable | Descripción | Default |
|----------|-------------|---------|
| `DEVICE_PATH` | `/dev/video0` fijo o autodetectar | — |
| `BACKEND` | `auto`, `fswebcam`, `ffmpeg` | `auto` |
| `WIDTH` / `HEIGHT` | Resolución de captura | `1280x720` |
| `JPEG_QUALITY` | Calidad 1–100 | `85` |
| `CAPTURE_TIMEOUT_SECONDS` | Timeout por captura | `15` |
| `CAPTURE_DIR` | Directorio de salida | `DATA_DIR/captures` |

Ver todas las opciones en `.env.example`.

### Comandos de prueba

```bash
# Listar cámaras detectadas
NILOCARDMED_DATA_DIR=./data python -m nilocardmed.main camera list
NILOCARDMED_DATA_DIR=./data python -m nilocardmed.main camera list --json

# Captura de prueba (autodetecta la primera cámara)
NILOCARDMED_DATA_DIR=./data python -m nilocardmed.main camera capture-test

# Captura explícita
NILOCARDMED_DATA_DIR=./data python -m nilocardmed.main camera capture-test \
  --device /dev/video0 \
  --output ./data/captures/test.jpg \
  --backend fswebcam

# Atajo directo
nilocardmed-camera capture-test --json
```

En Docker (con cámara mapeada en `docker-compose.pi.yml`):

```bash
docker compose exec nilocardmed python -m nilocardmed.main camera list
docker compose exec nilocardmed python -m nilocardmed.main camera capture-test
```

## Cliente REST hacia SER (Fase 3)

Módulo `nilocardmed/ser_client/` con cliente HTTP **totalmente parametrizable** hasta definir el contrato final con SER.

### Modos de payload (`NILOCARDMED_SER__PAYLOAD_MODE`)

| Modo | Descripción |
|------|-------------|
| `multipart` | `multipart/form-data` con campo imagen + metadatos |
| `json_base64` | JSON con imagen en base64 |
| `json_base64_data_uri` | JSON con `data:image/jpeg;base64,...` |
| `raw_binary` | Cuerpo binario JPEG |
| `octet_stream` | Cuerpo `application/octet-stream` |

### Autenticación (`NILOCARDMED_SER__AUTH_TYPE`)

`none`, `bearer`, `header`, `query`, `basic` — ver `.env.example`.

### Reintentos

Configurables: `MAX_RETRIES`, `RETRY_BACKOFF_SECONDS`, `RETRY_BACKOFF_MULTIPLIER`, `RETRY_ON_STATUS_CODES`, etc.

### Comandos de prueba

```bash
# Ver qué se enviaría (sin red)
NILOCARDMED_DATA_DIR=./data python -m nilocardmed.main ser send-test \
  --image ./data/captures/test.jpg --dry-run

# Enviar imagen existente
NILOCARDMED_SER__URL=http://ser.local/api/samples \
  python -m nilocardmed.main ser send-test --image ./data/captures/test.jpg

# Capturar y enviar en un paso
python -m nilocardmed.main ser send-test --json

# Atajo directo
nilocardmed-ser send-test --image ./data/captures/test.jpg
```

Cuando tengas el contrato de SER, bastará con ajustar variables en `.env` / `config.json` (URL, `PAYLOAD_MODE`, nombres de campos, auth).

## Muestreo periódico (Fase 4)

El daemon (`python -m nilocardmed.main run`) arranca el **motor de muestreo** en background: captura cada `INTERVAL_SECONDS` y envía a SER dentro de la ventana `[MONITOR_START, MONITOR_END]`.

### Configuración (`NILOCARDMED_SAMPLING__*`)

| Variable | Descripción | Default |
|----------|-------------|---------|
| `ENABLED` | Activa el muestreo en el daemon | `true` |
| `INTERVAL_SECONDS` | Segundos entre ciclos | `60` |
| `MONITOR_START` / `MONITOR_END` | Epoch Unix; `-1` = sin límite | `-1` |
| `AFTER_WINDOW_BEHAVIOR` | `stop` o `idle` al pasar `MONITOR_END` | `stop` |
| `UPLOAD_ENABLED` | Si `false`, solo captura | `true` |
| `CONFIG_RELOAD_SECONDS` | Recarga `config.json` periódicamente | `30` |
| `MAX_CONSECUTIVE_FAILURES` | Detiene tras N fallos (`0` = ilimitado) | `0` |

### Comandos

```bash
# Estado de ventana y configuración
python -m nilocardmed.main sampler status

# Un ciclo captura + envío
python -m nilocardmed.main sampler run-once

# Bucle en primer plano (pruebas)
python -m nilocardmed.main sampler run --max-cycles 3

# Atajo
nilocardmed-sampler status
```

Ventana de prueba (activa 60 s):

```bash
NILOCARDMED_SAMPLING__MONITOR_START=$(date +%s) \
NILOCARDMED_SAMPLING__MONITOR_END=$(($(date +%s)+60)) \
NILOCARDMED_SAMPLING__INTERVAL_SECONDS=10 \
python -m nilocardmed.main sampler run --max-cycles 2
```

## Gestión WiFi (Fase 5)

Módulo `nilocardmed/wifi/` con backends parametrizables:

| Backend | Uso |
|---------|-----|
| `host_script` | **Recomendado en Docker/Pi** — script montado + D-Bus + `network_mode: host` |
| `nmcli` | NetworkManager directo desde el contenedor |
| `mock` | Desarrollo sin hardware WiFi |
| `auto` | `host_script` → `nmcli` → `mock` |

### Despliegue Docker (Pi)

En `deploy.env`:

```bash
ENABLE_WIFI=true
WIFI_HOST_SCRIPT_HOST=./scripts/wifi-host.sh
WIFI_HOST_SCRIPT_CONTAINER=/host/scripts/wifi-host.sh
```

Regenera override: `./scripts/generate-compose-override.sh` (monta script + D-Bus + red host).

### Comandos

```bash
# Escanear (mock en dev)
NILOCARDMED_WIFI__BACKEND=mock python -m nilocardmed.main wifi scan

# Conectar y persistir en config.json
python -m nilocardmed.main wifi connect --ssid MiRed --password secret

# Estado y conectividad
python -m nilocardmed.main wifi status
python -m nilocardmed.main wifi test

nilocardmed-wifi scan
```

Script de host en la Pi: `scripts/wifi-host.sh` (requiere `nmcli` + NetworkManager).

## Bluetooth BLE/GATT (Fases 6–7.5)

Servicio BLE con protocolo JSON sobre GATT, autenticación por contraseña y operaciones de cámara, muestreo y WiFi vía Bluetooth. **Compatible con Web Bluetooth en tablet Android** (framing MTU + guía cliente).

- Protocolo: [docs/BLUETOOTH_PROTOCOL.md](docs/BLUETOOTH_PROTOCOL.md)
- App web Android: [docs/WEB_BLUETOOTH_CLIENT.md](docs/WEB_BLUETOOTH_CLIENT.md)

### Configuración (`NILOCARDMED_BLUETOOTH__*`)

| Variable | Descripción |
|----------|-------------|
| `ENABLED` | Activa servicio en el daemon |
| `DEVICE_NAME` | Nombre visible en escaneo BLE |
| `PASSWORD` | Contraseña del comando `auth` |
| `BACKEND` | `auto`, `bluez`, `mock` |
| `SERVICE_UUID` / `RX_*` / `TX_*` | UUIDs GATT |
| `CAPTURE_TEST_MODE` | `base64`, `path` o `chunked` (default) |
| `CAPTURE_CHUNK_SIZE` | Bytes por chunk de imagen (default 200) |
| `BLE_FRAMING_ENABLED` | Fragmenta respuestas grandes para Web Bluetooth |
| `BLE_MAX_NOTIFICATION_BYTES` | Tope por notificación ATT (default 512) |
| `BLE_FRAME_PAYLOAD_BYTES` | Payload JSON por frame (default 200) |

En Pi/Docker: `ENABLE_BLUETOOTH=true` en `deploy.env` (D-Bus + `network_mode: host`).

Instalar soporte BlueZ Python: `pip install nilocardmed[ble]`

### Comandos

```bash
# Probar auth + ping sin hardware
NILOCARDMED_DATA_DIR=/tmp/nilocardmed-test \
  python -m nilocardmed.main bluetooth test-session --json

# Suite Fase 7 (WiFi mock; omitir cámara si no hay USB)
NILOCARDMED_DATA_DIR=/tmp/nilocardmed-test \
  python -m nilocardmed.main bluetooth test-commands --skip-camera-capture

# Framing BLE (Web Bluetooth / MTU)
NILOCARDMED_DATA_DIR=/tmp/nilocardmed-test \
  python -m nilocardmed.main bluetooth test-framing

# Ver UUIDs
python -m nilocardmed.main bluetooth info

# Servidor GATT en primer plano (Pi con BlueZ)
python -m nilocardmed.main bluetooth run

nilocardmed-bluetooth test-framing
```

Comandos BLE disponibles: `camera_list`, `camera_capture_test`, `sampling_set_interval`, `sampling_set_window`, `wifi_scan`, `wifi_connect`, `wifi_status`, `wifi_test` (ver protocolo).

El daemon arranca Bluetooth en background si `BLUETOOTH__ENABLED=true`.

## CardMed (Fase 8)

Módulo `nilocardmed/cardmed/`: configuración de negocio y prueba end-to-end (captura → validación → SER).

Documentación BLE: comandos `cardmed_get`, `cardmed_configure`, `cardmed_test` en [docs/BLUETOOTH_PROTOCOL.md](docs/BLUETOOTH_PROTOCOL.md).

### Configuración (`NILOCARDMED_CARDMED__*`)

| Variable | Descripción |
|----------|-------------|
| `SITE_ID` | Identificador del sitio (sync a `ser.device_id` si aplica) |
| `DEVICE_LABEL` | Etiqueta legible del dispositivo |
| `LOCATION` | Ubicación física |
| `OPERATOR_ID` | Operador que configuró el sistema |
| `METADATA` / `EXTRA` | JSON extra en envíos SER |
| `TEST_*` | Criterios y comportamiento de la prueba |

### Comandos CLI

```bash
# Ver configuración
NILOCARDMED_DATA_DIR=./data python -m nilocardmed.main cardmed get

# Configurar
python -m nilocardmed.main cardmed configure \
  --site-id SITE-001 --device-label "Sala 3" --operator-id op-1

# Prueba (sin envío SER)
python -m nilocardmed.main cardmed test --dry-run

# Prueba vía Bluetooth (mock)
NILOCARDMED_DATA_DIR=/tmp/nilocardmed-test \
  python -m nilocardmed.main bluetooth test-commands --skip-camera-capture

nilocardmed-cardmed test --skip-upload --json
```

## Integración y endurecimiento (Fase 9)

Módulo `nilocardmed/resilience/`: salud del sistema, reconexión WiFi automática y muestreo tolerante a fallos.

- Despliegue Pi: [docs/DEPLOYMENT_PI.md](docs/DEPLOYMENT_PI.md)
- **Guía rápida de despliegue:** [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md)
- Operador (tablet): [docs/OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md)

### Configuración (`NILOCARDMED_RESILIENCE__*`)

| Variable | Descripción |
|----------|-------------|
| `ENABLED` | Supervisor de resiliencia en el daemon |
| `WIFI_RECONNECT_*` | Reconexión automática con SSID guardado |
| `PAUSE_SAMPLING_WITHOUT_WIFI` | No captura sin red |
| `PAUSE_SAMPLING_WITHOUT_CAMERA` | No captura sin cámara USB |
| `LOG_HEALTH_SUMMARY_INTERVAL_SECONDS` | Log periódico de salud |
| `SER_HEALTH_CHECK_ENABLED` | Probe HTTP a SER en health check |

### Comandos

```bash
# Informe de salud JSON
NILOCARDMED_DATA_DIR=./data python -m nilocardmed.main health status

# Exit code para Docker/cron (0=sano)
python -m nilocardmed.main health check --exit-code

# BLE: health_status (desde tablet)
# Ver docs/OPERATOR_GUIDE.md

# Scripts en Pi
./scripts/health-check.sh
./scripts/stress-test.sh 50 30

# Tests CI (x86)
pip install -e ".[dev]"
pytest -q
```

### Pi Zero W2

- Límite RAM contenedor: **384M** (`docker-compose.pi.yml`)
- Healthcheck Docker usa `health check --exit-code`
- Tras reinicio: systemd → Docker → auto WiFi → muestreo + BLE

## Almacenamiento, resolución y watchdog

### Política de almacenamiento (`NILOCARDMED_STORAGE__*`)

- Tras **upload OK** a SER: la foto se **elimina** del disco local.
- Si SER no está accesible: la captura pasa a cola **`pending/`** y se reintenta periódicamente.
- Si el espacio libre baja del **10%** (`MIN_FREE_PERCENT`): se borran las fotos **más antiguas** (pending + captures).

### Resolución de imagen (`NILOCARDMED_CAMERA__OUTPUT_*`)

- `WIDTH`/`HEIGHT`: resolución pedida a la cámara (V4L2).
- `OUTPUT_WIDTH`/`OUTPUT_HEIGHT`: tamaño objetivo tras captura (~1K por defecto: 1280×720).
- Si la cámara entrega más píxeles, se **reescala** con Pillow antes del envío.

### Watchdog (`NILOCARDMED_RESILIENCE__WATCHDOG_*`)

Si no hay ciclos exitosos en `WATCHDOG_MAX_STALE_SECONDS` (default 30 min), el proceso sale con código controlado; Docker (`unless-stopped`) lo relanza.

### BLE adicional

| Comando | Uso |
|---------|-----|
| `system_info` | Versión, uptime, disco |
| `storage_status` | Espacio y cola pending |
| `sampler_history` | Últimos ciclos de muestreo |
| `events_list` | Eventos del sistema |
| `time_get` / `time_sync` | Leer/ajustar hora desde tablet |

Ver [docs/BLUETOOTH_PROTOCOL.md](docs/BLUETOOTH_PROTOCOL.md).

### Endurecimiento adicional

| Mejora | Comportamiento |
|--------|----------------|
| Watchdog inteligente | No reinicia si el muestreo está pausado (WiFi, ventana horaria) |
| Config atómica + `secrets.json` | Contraseñas WiFi/BLE fuera de `config.json` (permisos 600) |
| Pending protegido | La purga por disco no toca `pending/` por defecto |
| Subida escalonada | 1 foto cada ≥45 s; fuera de ventana de monitorización drena la cola |
| Timestamp de captura | Las fotos pending conservan `captured_at` original al subir |
| Validación JPEG | Reintento automático (hasta 3×) si la captura es corrupta |
| Health `degraded` | WiFi en provisioning no marca el contenedor como caído |
| Telemetría JSONL | Eventos/ciclos persistidos en `DATA_DIR/telemetry.jsonl` |
| Comandos BLE sensibles | Requieren contraseña o sesión elevada (1 h tras `auth`) |
| Supervisor hilo sampler | Reinicia el hilo si muere inesperadamente |

