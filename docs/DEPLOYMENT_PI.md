# Despliegue en Raspberry Pi Zero W2

Guía de producción para NiloCardmed-dev en hardware real.

## Requisitos

| Componente | Detalle |
|------------|---------|
| Hardware | Raspberry Pi Zero W2 |
| SO | Raspberry Pi OS (64-bit o 32-bit) con Docker Engine + Compose v2 |
| Cámara | USB UVC compatible (`/dev/video0`) |
| Red | WiFi hacia SER |
| Bluetooth | BLE integrado (configuración con tablet Android) |

## Instalación rápida

```bash
git clone <repo> /opt/nilocardmed-src
cd /opt/nilocardmed-src

cp .env.example .env
cp deploy.env.example deploy.env
# Editar .env (SER, muestreo, CardMed, Bluetooth password)
# Editar deploy.env (rutas, ENABLE_WIFI, ENABLE_BLUETOOTH, VIDEO_DEVICE_HOST)

sudo ./scripts/install.sh
```

`install.sh`:

1. Carga `deploy.env`
2. Genera `docker-compose.override.yml` (USB, WiFi host, BLE)
3. Construye imagen `linux/arm/v7`
4. Instala unidad systemd

## Configuración recomendada en Pi

### `deploy.env`

```bash
DOCKER_DEFAULT_PLATFORM=linux/arm/v7
HOST_DATA_DIR=/var/lib/nilocardmed/data
HOST_LOG_DIR=/var/lib/nilocardmed/logs
VIDEO_DEVICE_HOST=/dev/video0
MOUNT_USB_BUS=true
ENABLE_WIFI=true
ENABLE_BLUETOOTH=true
CONTAINER_MEMORY_LIMIT=384M
RESTART_POLICY=unless-stopped
```

### `.env` (aplicación)

```bash
NILOCARDMED_WIFI__AUTO_CONNECT_ON_STARTUP=true
NILOCARDMED_WIFI__SSID=RedSER
NILOCARDMED_BLUETOOTH__PASSWORD=<contraseña-segura>
NILOCARDMED_SER__URL=https://ser.example/api/samples
NILOCARDMED_RESILIENCE__WIFI_RECONNECT_ENABLED=true
NILOCARDMED_RESILIENCE__PAUSE_SAMPLING_WITHOUT_WIFI=true
NILOCARDMED_SAMPLING__INTERVAL_SECONDS=60
NILOCARDMED_SAMPLING__MAX_CONSECUTIVE_FAILURES=0
```

`MAX_CONSECUTIVE_FAILURES=0` permite operación 24 h con reintentos indefinidos (recomendado con supervisor de resiliencia).

## Verificación post-instalación

```bash
sudo systemctl status nilocardmed
docker compose ps
docker compose logs -f --tail=100

# Salud del contenedor
./scripts/health-check.sh

# Pruebas locales en contenedor
docker compose exec nilocardmed python -m nilocardmed.main camera list
docker compose exec nilocardmed python -m nilocardmed.main health status
docker compose exec nilocardmed python -m nilocardmed.main cardmed test --skip-upload
```

## Pruebas de estrés (24 h)

```bash
# Muestreo prolongado (foreground, 50 ciclos)
./scripts/stress-test.sh 50 30

# Monitorización en paralelo
watch -n 30 './scripts/health-check.sh || true'
journalctl -u nilocardmed -f
```

Para prueba 24 h real, dejar el daemon en producción con ventana `MONITOR_START/MONITOR_END=-1` y revisar logs al día siguiente.

## Escenarios de fallo

| Escenario | Comportamiento |
|-----------|----------------|
| Sin WiFi | Supervisor reconecta cada 120 s; muestreo pausado si `PAUSE_SAMPLING_WITHOUT_WIFI=true` |
| Sin cámara | Ciclos omitidos; health reporta `camera` FAIL |
| SER caído | Reintentos HTTP configurables; fallos consecutivos en sampler |
| Reinicio Pi | systemd + `unless-stopped` levanta el stack; WiFi auto-connect si configurado |
| Poca memoria | Límite 384M en compose; health avisa si MemAvailable < umbral |

## Optimización Pi Zero W2

- Imagen slim + límite RAM 384M en `docker-compose.pi.yml`
- `MALLOC_ARENA_MAX=2` reduce fragmentación glibc
- Captura MJPEG nativa (`INPUT_FORMAT=mjpeg`) reduce CPU vs raw
- Resolución 1280x720 o 640x480 según necesidad
- `CONFIG_RELOAD_SECONDS=30` recarga cambios Bluetooth sin reiniciar

## Actualización

```bash
cd /opt/nilocardmed-src
git pull
sudo ./scripts/install.sh
```

## Desinstalación

```bash
sudo ./scripts/uninstall.sh
```

Conserva `HOST_DATA_DIR` y `HOST_LOG_DIR` salvo borrado manual.

## Documentación relacionada

- [OPERATOR_GUIDE.md](OPERATOR_GUIDE.md) — operador con tablet Android
- [BLUETOOTH_PROTOCOL.md](BLUETOOTH_PROTOCOL.md) — protocolo BLE
- [WEB_BLUETOOTH_CLIENT.md](WEB_BLUETOOTH_CLIENT.md) — integración app web
