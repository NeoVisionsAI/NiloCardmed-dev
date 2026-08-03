# Protocolo Bluetooth NiloCardmed (BLE GATT)

Este documento describe el protocolo JSON entre apps cliente y NiloCardmed-dev.

## Transporte GATT

| Elemento | UUID por defecto | Dirección |
|----------|------------------|-----------|
| Servicio | `6e400010-b5a3-f393-e0a9-e50e24dcca9e` | — |
| RX (escritura cliente → dispositivo) | `6e400011-b5a3-f393-e0a9-e50e24dcca9e` | Write / Write Without Response |
| TX (lectura/notificación dispositivo → cliente) | `6e400012-b5a3-f393-e0a9-e50e24dcca9e` | Read / Notify |

Todos los UUIDs son configurables vía `NILOCARDMED_BLUETOOTH__*`.

### Flujo recomendado

1. Escanear BLE y conectar al dispositivo con `local_name` configurado (default: `NiloCardmed`).
2. Suscribirse a **notificaciones** en TX.
3. Escribir peticiones JSON en RX.
4. Leer la respuesta en la notificación TX (o Read en TX si no hay notify).

## Formato de mensajes

### Petición (cliente → dispositivo)

```json
{
  "cmd": "nombre_comando",
  "id": "identificador-opcional",
  "token": "token-opcional",
  "...": "campos específicos del comando"
}
```

- `cmd` (string, requerido): nombre del comando.
- `id` (string, opcional): correlación petición/respuesta.
- `token` (string, opcional): token emitido por `auth` (requerido en comandos protegidos).

### Respuesta (dispositivo → cliente)

```json
{
  "ok": true,
  "cmd": "nombre_comando",
  "id": "identificador-opcional",
  "data": {},
  "error": "mensaje si ok=false"
}
```

Los errores de negocio usan códigos estables (`invalid_password`, `camera_error`, `wifi_error`, etc.). Si hay detalle adicional, el formato es `codigo: mensaje`.

## Autenticación

Contraseña configurable (`NILOCARDMED_BLUETOOTH__PASSWORD`).

### `auth` (sin token previo)

**Petición:**

```json
{"cmd":"auth","password":"changeme","id":"1"}
```

**Respuesta OK:**

```json
{
  "ok": true,
  "cmd": "auth",
  "id": "1",
  "data": {
    "token": "…",
    "expires_in": 3600,
    "device_name": "NiloCardmed"
  }
}
```

**Errores:** `invalid_password`, `unauthorized` en comandos posteriores sin token válido.

El token expira tras `token_ttl_seconds` (default 3600 s).

## Comandos base

### `ping` (requiere token si `require_auth=true`)

```json
{"cmd":"ping","token":"…","id":"2"}
```

**Respuesta OK:** `{"pong": true, "version": "0.1.0"}`

### `commands_list`

Devuelve la lista de comandos registrados (incluye alias).

```json
{"cmd":"commands_list","token":"…","id":"3"}
```

## Comandos Fase 7 — Cámara

### `camera_list` (alias: `list_cameras`)

**Petición:**

```json
{"cmd":"camera_list","token":"…","include_non_capture":false,"id":"4"}
```

**Respuesta OK:**

```json
{
  "cameras": [
    {
      "id": "video0",
      "path": "/dev/video0",
      "name": "USB Camera",
      "driver": "uvcvideo",
      "bus_info": "usb-…",
      "supports_capture": true
    }
  ]
}
```

### `camera_capture_test` (alias: `capture_test`)

Captura una imagen JPEG de prueba.

**Petición:**

```json
{
  "cmd": "camera_capture_test",
  "token": "…",
  "device": "/dev/video0",
  "mode": "chunked",
  "id": "5"
}
```

- `device` (opcional): ruta V4L2; si se omite, autodetecta.
- `mode` (opcional): `base64`, `path` o `chunked` (default: `capture_test_mode` en config).

**Modo `chunked` (recomendado BLE):** devuelve metadatos (`capture_id`, `size_bytes`, `sha256`, `total_chunks`, …) sin embeber la imagen.

**Modo `path`:** devuelve la ruta en disco del dispositivo (útil en operación local).

**Modo `base64`:** incluye `image_base64` si cabe en `max_image_response_bytes`.

### `camera_capture_chunk`

Lee un fragmento de la última captura en caché.

```json
{"cmd":"camera_capture_chunk","token":"…","capture_id":"abc123","index":0,"id":"6"}
```

**Respuesta OK:**

```json
{
  "capture_id": "abc123",
  "index": 0,
  "total_chunks": 42,
  "chunk_size": 384,
  "chunk_base64": "…"
}
```

## Comandos Fase 7 — Muestreo

### `sampling_get`

Devuelve configuración actual y evaluación de la ventana temporal.

### `sampling_set_interval` (alias: `set_interval`)

```json
{"cmd":"sampling_set_interval","token":"…","interval_seconds":120,"id":"7"}
```

Persiste en `config.json`.

### `sampling_set_window` (alias: `set_monitor_window`)

```json
{
  "cmd": "sampling_set_window",
  "token": "…",
  "monitor_start": 1700000000,
  "monitor_end": -1,
  "id": "8"
}
```

Valores `-1` = sin límite. Persiste en `config.json`.

## Comandos Fase 7 — WiFi

### `wifi_scan`

Lista redes visibles.

### `wifi_connect` (alias: `wifi_configure`)

```json
{
  "cmd": "wifi_connect",
  "token": "…",
  "ssid": "MiRed",
  "password": "secreto",
  "persist": true,
  "id": "9"
}
```

- `persist` (default `true`): guarda SSID/contraseña en `config.json`.

### `wifi_status`

```json
{"cmd":"wifi_status","token":"…","check_connectivity":true,"id":"10"}
```

### `wifi_test`

Comprueba conectividad HTTP (`connectivity_check_url`).

## Comandos Fase 8 — CardMed

### `cardmed_get` (alias: `get_cardmed_config`)

Devuelve configuración CardMed, `ser.device_id` y URL SER.

### `cardmed_configure` (alias: `configure_cardmed`, `configurar`)

Aplica configuración parcial y persiste en `config.json`.

```json
{
  "cmd": "cardmed_configure",
  "token": "…",
  "site_id": "SITE-001",
  "device_label": "Sala 3",
  "operator_id": "op-42",
  "metadata": {"ward": "cardiology"},
  "id": "21"
}
```

**Errores:** `cardmed_config_error`

### `cardmed_test` (alias: `probar_cardmed`, `test_cardmed`, `probar`)

Prueba end-to-end con pasos detallados en `data.steps` (WiFi → conectividad → captura → validación → SER).

```json
{"cmd":"cardmed_test","token":"…","skip_upload":false,"dry_run":false,"id":"22"}
```

`data.success=false` indica fallo; revisar `steps[]` para feedback al operador.

### `health_status` (alias: `health`, `system_health`)

Informe de salud del dispositivo (WiFi, cámara, SER, muestreo, disco, memoria).

```json
{"cmd":"health_status","token":"…","id":"30"}
```

## Comandos sistema, almacenamiento y telemetría

### `system_info`

Versión, uptime, disco, memoria e hint de actualización.

```json
{"cmd":"system_info","token":"…","id":"40"}
```

### `storage_status`

Espacio libre, umbral de purga (`min_free_percent`) y contadores de cola `pending`.

```json
{"cmd":"storage_status","token":"…","id":"41"}
```

### `sampler_history`

Últimos ciclos de muestreo (buffer en memoria).

```json
{"cmd":"sampler_history","token":"…","limit":20,"id":"42"}
```

### `events_list`

Eventos recientes (WiFi, storage, watchdog, time_sync…).

```json
{"cmd":"events_list","token":"…","limit":50,"id":"43"}
```

### `time_get`

Hora actual del dispositivo (UTC).

```json
{"cmd":"time_get","token":"…","id":"44"}
```

**Respuesta:** `{"epoch":…,"iso8601":"…"}`

### `time_sync`

Sincroniza la hora del sistema desde el tablet. Requiere `cap_add: SYS_TIME` en Docker.

```json
{"cmd":"time_sync","token":"…","password":"…","epoch":1735689600,"id":"45"}
```

**Comandos privilegiados** (requieren contraseña en la petición o sesión elevada tras `auth` válida durante 1 h):

`wifi_connect`, `time_sync`, `cardmed_configure`, `sampling_set_interval`, `sampling_set_window`

**Errores:** `privileged_auth_required`, `time_sync_failed`

## Extensibilidad (Fase 9+)

Nuevos comandos se registran en `nilocardmed/bluetooth/handlers.py` y `nilocardmed/cardmed/handlers.py`. Convenciones:

- Un comando = un handler (+ alias opcionales).
- Respuesta siempre con envelope `ok/cmd/id/data/error`.
- Comandos protegidos exigen `token` válido salvo `allowed_commands_without_auth`.

## Transporte BLE — framing (Fase 7.5, Web Bluetooth Android)

Las notificaciones ATT suelen limitarse a **~512 bytes**. Si una respuesta JSON es mayor, el dispositivo la divide en varios frames:

```json
{"t":"f","i":0,"n":3,"d":"fragmento del JSON completo"}
```

| Campo | Significado |
|-------|-------------|
| `t` | `"f"` = frame de transporte |
| `i` | Índice (0-based) |
| `n` | Total de frames |
| `d` | Fragmento UTF-8 del JSON de respuesta |

- Respuestas que caben en una notificación se envían **sin** wrapper (JSON directo).
- El cliente debe **suscribirse a TX notify** y reensamblar todos los frames antes de `JSON.parse`.
- Las peticiones RX grandes (futuro) pueden usar el mismo formato de frames.

Guía completa para la app web Android: [WEB_BLUETOOTH_CLIENT.md](WEB_BLUETOOTH_CLIENT.md)

## Límites

| Variable | Default | Uso |
|----------|---------|-----|
| `max_message_bytes` | 512 | Petición RX (JSON completo reensamblado) |
| `max_response_bytes` | 4096 | Respuestas genéricas (antes de framing) |
| `max_image_response_bytes` | 32768 | `camera_capture_test` mode=base64 |
| `max_chunk_response_bytes` | 4096 | `camera_capture_chunk` (antes de framing) |
| `capture_chunk_size` | 200 | Bytes JPEG por chunk lógico |
| `ble_framing_enabled` | true | Activa fragmentación TX |
| `ble_max_notification_bytes` | 512 | Tope por notificación BLE |
| `ble_frame_payload_bytes` | 200 | Payload JSON por frame |
| `ble_inter_frame_delay_ms` | 15 | Pausa entre notifies |

Codificación: UTF-8 JSON compacto.

## Prueba sin hardware

```bash
# Auth + ping
NILOCARDMED_DATA_DIR=/tmp/nilocardmed-test \
  python -m nilocardmed.main bluetooth test-session

# Suite Fase 7 (WiFi mock; cámara opcional)
NILOCARDMED_DATA_DIR=/tmp/nilocardmed-test \
  python -m nilocardmed.main bluetooth test-commands --skip-camera-capture --json

# Framing BLE (Web Bluetooth)
NILOCARDMED_DATA_DIR=/tmp/nilocardmed-test \
  python -m nilocardmed.main bluetooth test-framing --json
```

## Configuración relevante

Ver `.env.example`: `DEVICE_NAME`, `BACKEND`, UUIDs, `REQUIRE_AUTH`, `TOKEN_TTL_SECONDS`, `CAPTURE_*`, `BLE_*`.
