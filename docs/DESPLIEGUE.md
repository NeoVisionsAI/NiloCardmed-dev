# Guía de despliegue — NiloCardmed

Instalación paso a paso en **Raspberry Pi Zero W2**. Hay dos momentos distintos:

| Momento | Quién | Qué se hace |
|---------|-------|-------------|
| **Fábrica / instalación** | Técnico (una vez) | Flash Raspberry Pi OS, clonar repo, ejecutar `install.sh` (instala todo) |
| **Campo / puesta en marcha** | Operador con tablet | Conectar por BLE y configurar WiFi, muestreo, CardMed, etc. |

La Pi Zero W2 ya trae **WiFi y Bluetooth integrados** — no hay que instalar hardware extra. Solo hay que activarlos en `deploy.env` para que el contenedor Docker pueda usarlos.

---

## Resumen en 5 pasos (instalación en fábrica)

1. Raspberry Pi OS + acceso a Internet + cámara USB opcional al instalar.
2. Clonar el repo en la Pi (cualquier ruta, p. ej. `~/dev/NiloCardmed-dev`).
3. **`sudo ./scripts/install.sh`** — instala todo en **`/opt/nilocardmed`**, configura systemd y arranca.
4. Revisar **`/opt/nilocardmed/.env`** → `NILOCARDMED_SER__URL` si hace falta.
5. Entregar: operador configura WiFi/muestreo por tablet (BLE).

> **Un solo comando:** desde el clone, `sudo ./scripts/install.sh` copia a `/opt/nilocardmed`, crea grupos, Docker, uuid, contraseña BLE y verifica que el contenedor arranca (sin cámara conectada = OK).

---

## 1. Requisitos previos

| Qué necesitas | Detalle |
|---------------|---------|
| Hardware | Raspberry Pi Zero W2 (WiFi + BLE integrados) |
| Cámara | USB UVC — puede conectarse/desconectarse; no bloquea el arranque |
| Almacenamiento | microSD ≥ 32 GB (recomendado 128 GB) |
| Red | Internet en la instalación (apt + descarga imagen Docker); WiFi operativo se configura después por tablet |
| Tablet | Android con BLE + app web (ver [OPERATOR_GUIDE.md](OPERATOR_GUIDE.md)) |
| Software en la Pi | **Raspberry Pi OS** (Debian). Docker y el resto los instala **`install.sh`** |

### Qué instala `install.sh` automáticamente

- Paquetes **apt**: `python3`, `rsync`, `openssl`, `uuid-runtime`, `bluez`, `dbus`, `network-manager`, `v4l-utils`, `git`, …
- **Docker Engine** + **Compose v2** (script oficial get.docker.com)
- Servicios: `docker`, `bluetooth`, `NetworkManager`, `dbus`
- Grupos del usuario (`docker`, `video`, `bluetooth`, `dialout`, `plugdev`)
- Configuración NiloCardmed: `.env`, `deploy.env`, uuid, systemd, build

Si Docker ya está instalado: `sudo ./scripts/install.sh --skip-host-deps`

Comprobar tras instalar:

```bash
docker --version
docker compose version
ls -l /dev/video*
```

---

## 2. Obtener el código

```bash
sudo mkdir -p /opt/nilocardmed-src
sudo chown "$USER:$USER" /opt/nilocardmed-src
git clone <URL-DEL-REPO> /opt/nilocardmed-src
cd /opt/nilocardmed-src
```

---

## 3. Configuración (solo dos ficheros)

### 3.1 Copiar plantillas (automático con `install.sh`)

**`sudo ./scripts/install.sh`** crea `deploy.env` y `.env` desde las plantillas, activa WiFi/BLE, genera un **uuid único por dispositivo** (persistente en `HOST_DATA_DIR/device-identity.env`), lo usa como **`NILOCARDMED_SER__DEVICE_ID`** en las peticiones al servidor y como nombre BLE **`NiloCardmed-<uuid>`**, y pide la **contraseña Bluetooth** por consola (Enter = genera una y la muestra).

Manual (solo si no usas `install.sh`):

```bash
cp .env.example .env
cp deploy.env.example deploy.env
```

### 3.2 Editar `deploy.env` (Docker + hardware)

Valores mínimos recomendados en Pi Zero W2:

```bash
NILOCARDMED_INSTALL_DIR=/opt/nilocardmed
HOST_DATA_DIR=/var/lib/nilocardmed/data
HOST_LOG_DIR=/var/lib/nilocardmed/logs
DOCKER_DEFAULT_PLATFORM=linux/arm/v7
ENABLE_CAMERA_HOTPLUG=true
VIDEO_DEVICE_HOST=/dev/video0
MOUNT_USB_BUS=true
ENABLE_WIFI=true
ENABLE_BLUETOOTH=true
CONTAINER_MEMORY_LIMIT=384M
RESTART_POLICY=unless-stopped
```

### 3.3 Editar `.env` (aplicación) — mínimo en fábrica

Con **`install.sh`**, la contraseña BLE y el nombre del dispositivo se configuran en la instalación. Solo conviene revisar **`NILOCARDMED_SER__URL`** (antes o después del install):

```bash
nano .env   # NILOCARDMED_SER__URL=https://tu-servidor/api/samples
```

En **reinstalaciones**, el uuid del dispositivo se mantiene si existe `device-identity.env` (mismo id en SER y en el nombre BLE). Al pedir contraseña BLE: si ya hay una configurada, **Enter** o **10 s sin escribir** = mantener la actual; en la **primera instalación** hay que introducirla obligatoriamente.

**No hace falta poner WiFi aquí.** El operador lo configurará desde la tablet (`wifi_scan` → `wifi_connect`). Lo mismo con intervalo de muestreo, ventana horaria y datos CardMed.

Si configuras `.env` a mano (sin `install.sh`), lo imprescindible **antes del primer arranque**:

```bash
# Contraseña BLE — el operador la usará en la app
NILOCARDMED_BLUETOOTH__PASSWORD=tu-contraseña-segura

# Servidor SER (destino de las fotos)
NILOCARDMED_SER__URL=https://tu-servidor/api/samples
# NILOCARDMED_SER__DEVICE_ID=...   ← install.sh lo genera; CardMed puede cambiarlo vía tablet

# Muestreo (valores por defecto razonables; el operador puede cambiarlos por BLE)
NILOCARDMED_SAMPLING__INTERVAL_SECONDS=60
NILOCARDMED_SAMPLING__MAX_CONSECUTIVE_FAILURES=0

# Logs
NILOCARDMED_LOG_LEVEL=INFO
NILOCARDMED_LOG_STRUCTURED=true
```

Opcional en `.env` (solo si quieres preconfigurar WiFi sin usar la tablet):

```bash
# NILOCARDMED_WIFI__SSID=NombreDeTuRed
# NILOCARDMED_WIFI__PASSWORD=clave-wifi
# NILOCARDMED_WIFI__AUTO_CONNECT_ON_STARTUP=true
```

> **Tip:** Con `MAX_CONSECUTIVE_FAILURES=0` el muestreo no se detiene por fallos puntuales (recomendado en producción).

---

## 3.4 Flujo operador (tablet) — ya soportado

Tras `install.sh`, el dispositivo arranca con **Bluetooth activo**. El operador:

1. Enciende la Pi (cámara USB conectada).
2. Abre la app en la tablet → escanea BLE → conecta a **NiloCardmed**.
3. Introduce la **contraseña Bluetooth** (la de `.env`).
4. Configura desde la app:

| Qué configurar | Comando BLE | Notas |
|----------------|-------------|-------|
| WiFi | `wifi_scan`, `wifi_connect` | Con `persist: true` (default) guarda SSID/clave en disco |
| Intervalo de muestreo | `sampling_set_interval` | Requiere contraseña / sesión elevada |
| Ventana horaria | `sampling_set_window` | Inicio/fin monitorización (epoch) |
| CardMed (sitio, etiqueta…) | `cardmed_configure` | Metadatos para SER |
| Hora del dispositivo | `time_sync` | Desde la tablet |
| Comprobar todo | `health_status`, `cardmed_test` | Prueba captura + envío |

Tras conectar WiFi por BLE, las credenciales quedan en `config.json` y el **supervisor reconecta solo** si se pierde la red. No hace falta volver a editar `.env`.

Guía detallada para el operador: [OPERATOR_GUIDE.md](OPERATOR_GUIDE.md) · Protocolo BLE: [BLUETOOTH_PROTOCOL.md](BLUETOOTH_PROTOCOL.md)

---

## 4. Copiar el proyecto a la Pi (desde tu PC)

Si desarrollas en otro equipo y la Pi solo tiene SSH:

```bash
# Sustituye PI_HOST (ej. 192.168.1.50 o raspberrypi.local)
export PI_HOST=pi@192.168.1.50

rsync -av --progress \
  --exclude .git --exclude .venv --exclude data --exclude __pycache__ \
  ./ "${PI_HOST}:/opt/nilocardmed/"

ssh "${PI_HOST}" 'cd /opt/nilocardmed && chmod +x scripts/pi-start.sh'
```

En la Pi, edita antes de arrancar (solo una vez):

```bash
ssh "${PI_HOST}"
cd /opt/nilocardmed
nano .env          # contraseña BLE + URL SER
# deploy.env ya trae ENABLE_WIFI/BLUETOOTH=true
```

---

## 5. Arrancar con `scripts/pi-start.sh`

Script único de comprobación y arranque:

```bash
cd /opt/nilocardmed   # o donde hayas clonado el repo
chmod +x scripts/pi-start.sh

./scripts/pi-start.sh check          # comprobar Docker, cámara, D-Bus, BLE…
./scripts/pi-start.sh start          # build + docker compose up -d
./scripts/pi-start.sh start --build  # forzar rebuild de imagen
./scripts/pi-start.sh status         # estado + health JSON
./scripts/pi-start.sh logs           # logs en tiempo real

# Arranque automático al boot (opcional, producción):
sudo ./scripts/pi-start.sh install
```

La **primera build en Pi Zero puede tardar 15–25 minutos**. Tras `start`, el dispositivo queda en BLE (`NiloCardmed`) listo para la tablet — **sin WiFi configurado es normal** (estado `degraded`).

---

## 6. Instalar con systemd (alternativa)

Desde el directorio del proyecto:

```bash
sudo ./scripts/install.sh
```

El script hace automáticamente:

- Crea directorios en el host (`/var/lib/nilocardmed/data`, `logs`, …).
- Genera `docker-compose.override.yml` (USB, WiFi, Bluetooth).
- Construye la imagen Docker para ARM.
- Instala y arranca el servicio **systemd** `nilocardmed`.

Instalación en otra ruta:

```bash
sudo INSTALL_DIR=/opt/nilocardmed ./scripts/install.sh
```

---

## 7. Verificación post-instalación

### Servicio y contenedor

```bash
sudo systemctl status nilocardmed
cd /opt/nilocardmed   # o tu INSTALL_DIR
docker compose ps
```

El contenedor debe estar **Up** y **healthy** (healthcheck cada 30 s).

### Salud del sistema

```bash
./scripts/health-check.sh
# o con más detalle:
docker compose exec nilocardmed python -m nilocardmed.main health status
```

Estados posibles:

| Estado | Significado |
|--------|-------------|
| `healthy` | Todo correcto |
| `degraded` | Funciona con limitaciones (p. ej. WiFi desconectado en provisioning) |
| `unhealthy` | Problema grave (cámara, disco, sampler caído…) |

### Prueba rápida de cámara

```bash
docker compose exec nilocardmed python -m nilocardmed.main camera list
docker compose exec nilocardmed python -m nilocardmed.main cardmed test --skip-upload
```

### Configuración desde tablet

1. Escanear BLE → dispositivo **NiloCardmed**.
2. Autenticarse con la contraseña de `.env`.
3. Conectar WiFi, probar captura, revisar `system_info` / `health_status`.

Guía operador: [OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

---

## 8. Configuración posterior (sin reinstalar)

| Cambio | Cómo aplicarlo |
|--------|----------------|
| WiFi, intervalo, ventana horaria | App tablet vía BLE → se guarda en `/var/lib/nilocardmed/data/config.json` |
| URL SER, contraseña BLE | Editar `.env` → `sudo systemctl restart nilocardmed` |
| Hardware (cámara, BLE, WiFi host) | Editar `deploy.env` → `sudo ./scripts/install.sh` |

La config se recarga sola cada ~30 s; reinicio solo necesario para cambios en `.env` / `deploy.env`.

---

## 9. Actualizar versión

```bash
cd /opt/nilocardmed-src   # o donde tengas el clone
git pull
sudo ./scripts/install.sh
```

Los datos (`/var/lib/nilocardmed/data`) y logs se conservan.

---

## 10. Desinstalar

```bash
sudo ./scripts/uninstall.sh
```

Los datos en `HOST_DATA_DIR` y logs en `HOST_LOG_DIR` **no se borran** salvo que lo hagas manualmente.

---

## 11. Observabilidad y logs

Sí, hay un **sistema de logs** integrado. Puedes revisar qué ocurre en varios sitios según lo que busques.

### Dónde están los logs

| Ubicación | Contenido | Cómo verlo |
|-----------|-----------|------------|
| **Docker stdout** | Log principal en tiempo real (JSON o texto) | `docker compose logs -f --tail=200` |
| **systemd journal** | Arranque/parada del stack Docker | `journalctl -u nilocardmed -f` |
| **Fichero rotativo** | `/var/lib/nilocardmed/logs/nilocardmed.log` | `tail -f /var/lib/nilocardmed/logs/nilocardmed.log` |
| **Telemetría** | `/var/lib/nilocardmed/data/telemetry.jsonl` | Ciclos, eventos, reinicios, purgas |
| **Healthcheck Docker** | Estado healthy/degraded/unhealthy | `docker compose ps` |

Variables de control (en `.env`):

```bash
NILOCARDMED_LOG_LEVEL=INFO          # DEBUG para diagnóstico
NILOCARDMED_LOG_STRUCTURED=true     # true = JSON (recomendado)
# NILOCARDMED_LOG_DIR=/var/log/nilocardmed   # ya configurado en Docker
```

El fichero rotativo guarda hasta **3 copias** de **5 MB** cada una.

### Ver la traza operativa por SSH

Los eventos importantes se registran con el logger **`nilocardmed.trace`** y el prefijo **`[ble]`**, **`[wifi]`**, **`[config]`**, **`[system]`** en el mensaje. También quedan en `data/telemetry.jsonl`.

```bash
cd /opt/nilocardmed

# Solo traza (BLE, WiFi, cambios de config) — recomendado mientras configuras la tablet
./scripts/pi-start.sh trace

# Todos los logs del contenedor
./scripts/pi-start.sh logs
# o directamente:
docker compose logs -f --tail=200

# Filtrar manualmente (logs JSON)
docker compose logs -f 2>&1 | grep nilocardmed.trace

# Historial persistido (eventos recientes)
tail -f /var/lib/nilocardmed/data/telemetry.jsonl
```

**Ejemplos de mensajes que verás:**

| Mensaje | Significado |
|---------|-------------|
| `[system] arranque` | Servicio iniciado |
| `[system] bluetooth_activo` | GATT BLE publicado (`NiloCardmed`) |
| `[ble] cliente BLE conectado` | Tablet suscrita a notificaciones |
| `[ble] autenticación BLE OK` | Operador autenticado |
| `[ble] comando BLE wifi_connect OK` | WiFi configurado |
| `[config] WiFi conectado por BLE` | SSID guardado (ssid=…) |
| `[config] intervalo de muestreo` | Cambio de intervalo |
| `[wifi] Conectando a WiFi ssid=…` | Conexión WiFi en curso |

Los comandos muy repetitivos (`ping`, chunks de imagen) van a **DEBUG** y no saturan la traza.

### Qué se registra automáticamente

- **Arranque:** versión, rutas, resumen de configuración (sin secretos).
- **Muestreo:** capturas OK/fallidas, envíos a SER, cola `pending`, purgas de disco.
- **Resiliencia:** reconexión WiFi, resumen de salud cada 5 min (WARNING si degradado).
- **Errores:** captura corrupta, SER inaccesible, JPEG inválido, watchdog, reinicio de hilos.
- **Bluetooth:** operaciones vía telemetría (`events_list` desde la tablet).

Ejemplos de mensajes útiles al buscar fallos:

```bash
# Errores recientes
docker compose logs --tail=500 | grep -iE 'error|warning|failed|fallid'

# Solo JSON (con jq instalado)
docker compose logs --tail=50 | jq -r 'select(.level) | "\(.timestamp) \(.level) \(.message)"'

# Telemetría en disco
tail -20 /var/lib/nilocardmed/data/telemetry.jsonl
```

### Comprobar estado sin leer logs

```bash
# Salud agregada
docker compose exec nilocardmed python -m nilocardmed.main health status

# Desde tablet BLE
#   health_status, system_info, storage_status
#   sampler_history, events_list
```

### Exportar logs para soporte

```bash
cd /opt/nilocardmed
docker compose logs --tail=500 > /tmp/nilocardmed-docker.log
cp /var/lib/nilocardmed/logs/nilocardmed.log /tmp/ 2>/dev/null || true
cp /var/lib/nilocardmed/data/telemetry.jsonl /tmp/ 2>/dev/null || true
docker compose exec nilocardmed python -m nilocardmed.main health status > /tmp/nilocardmed-health.json
```

Enviar esos ficheros **sin** incluir `.env` ni `secrets.json`.

---

## 12. Solución rápida de problemas

| Síntoma | Qué revisar |
|---------|-------------|
| Contenedor no arranca | `journalctl -u nilocardmed -n 50` |
| Sin cámara | `ls /dev/video0`, logs `camera` |
| No sube fotos | logs `SER` / `pending`; `storage_status` por BLE |
| BLE no visible | `ENABLE_BLUETOOTH=true` en `deploy.env`, reinstalar |
| WiFi no conecta | BLE `wifi_scan` / `wifi_connect`; logs `WiFi` |
| Disco lleno | `storage_status`; carpeta `data/pending/` |
| Reinicios frecuentes | `telemetry.jsonl` → eventos `watchdog_restart` |

Documentación técnica ampliada: [DEPLOYMENT_PI.md](DEPLOYMENT_PI.md).

---

## Checklist final

**Fábrica (técnico)**

- [ ] Raspberry Pi OS + Docker + Compose
- [ ] Cámara en `/dev/video0`
- [ ] `.env` con contraseña BLE y URL SER (sin WiFi)
- [ ] `deploy.env` con `ENABLE_WIFI=true` y `ENABLE_BLUETOOTH=true`
- [ ] `sudo ./scripts/install.sh` sin errores
- [ ] `docker compose ps` → **Up** (healthy o degraded sin WiFi es normal)

**Campo (operador con tablet)**

- [ ] App conectada por BLE + autenticación OK
- [ ] WiFi configurado (`wifi_connect` con persist)
- [ ] `wifi_test` / `health_status` → conectividad OK
- [ ] CardMed y muestreo configurados
- [ ] `cardmed_test` → captura y envío OK
