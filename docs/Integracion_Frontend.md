# Integración frontend — NiloCardmed vía Web Bluetooth

Documento **autocontenido** para integrar una plataforma web (PWA / tablet Android) con un dispositivo **NiloCardmed** (Raspberry Pi) usando **BLE GATT** y un protocolo **JSON** sobre características GATT.

---

## 1. Resumen

| Aspecto | Detalle |
|---------|---------|
| Transporte | BLE GATT (Web Bluetooth API) |
| Formato | JSON UTF-8 |
| Dirección RX | Cliente **escribe** comandos |
| Dirección TX | Cliente **lee / recibe notify** respuestas |
| Autenticación | Contraseña BLE → token temporal |
| Fragmentación | Respuestas grandes en frames `{t:"f", i, n, d}` |
| Imágenes | Modo `chunked` + comando `camera_capture_chunk` |

---

## 2. Requisitos de la plataforma web

| Plataforma | Navegador | Soporte |
|------------|-----------|---------|
| Tablet Android | Chrome, Edge, Samsung Internet | Sí (Web Bluetooth) |
| iOS / iPad | Safari | **No** (Web Bluetooth no disponible) |
| Escritorio | Chrome (con adaptador BLE) | Parcial |

**Obligatorio:**

- App servida por **HTTPS** (excepción: `http://localhost` en desarrollo).
- Conexión BLE solo tras **gesto del usuario** (tap en botón).
- Permisos Bluetooth concedidos en el dispositivo.

---

## 3. Descubrimiento y nombre del dispositivo

Cada NiloCardmed tiene un nombre BLE único:

```text
NiloCardmed-<uuid>
```

Ejemplo: `NiloCardmed-d212bd98`.

> **Importante:** El hostname de la Raspberry puede ser `cardmed`. Algunos escaneos muestran ese alias en lugar del nombre GATT. **Conecta siempre filtrando por prefijo `NiloCardmed`** o por el UUID de servicio.

### 3.1 Listar / seleccionar dispositivo (Web Bluetooth)

Web Bluetooth **no permite escanear en segundo plano** sin interacción. El flujo estándar es un diálogo del sistema:

```javascript
const SERVICE_UUID = '6e400010-b5a3-f393-e0a9-e50e24dcca9e';

async function pickDevice() {
  return navigator.bluetooth.requestDevice({
    // Opción A: prefijo de nombre (varios dispositivos en almacén)
    filters: [{ namePrefix: 'NiloCardmed' }],
    // Opción B: un dispositivo conocido
    // filters: [{ name: 'NiloCardmed-d212bd98' }],
    optionalServices: [SERVICE_UUID],
  });
}
```

Si la app debe mostrar una lista propia, la única vía con Web Bluetooth es:

1. Botón «Buscar dispositivos» → `requestDevice` (diálogo nativo).
2. Guardar `device.id` + `device.name` en `localStorage` para reconexiones.
3. Reconectar con el mismo objeto `BluetoothDevice` si la pestaña no se cerró, o repetir `requestDevice`.

**No existe API estándar** para listar todos los BLE cercanos sin diálogo del SO.

---

## 4. UUIDs GATT (valores por defecto)

| Rol | UUID |
|-----|------|
| Servicio | `6e400010-b5a3-f393-e0a9-e50e24dcca9e` |
| RX — cliente → Pi (write) | `6e400011-b5a3-f393-e0a9-e50e24dcca9e` |
| TX — Pi → cliente (read + notify) | `6e400012-b5a3-f393-e0a9-e50e24dcca9e` |

En producción pueden cambiar (configuración del dispositivo). La app debe permitir override manual en ajustes avanzados.

**Apariencia BLE:** código `833` (Generic Sensor).

---

## 5. Conexión GATT

### 5.1 Conectar

```javascript
const RX_UUID = '6e400011-b5a3-f393-e0a9-e50e24dcca9e';
const TX_UUID = '6e400012-b5a3-f393-e0a9-e50e24dcca9e';

async function connect(device) {
  const server = await device.gatt.connect();
  const service = await server.getPrimaryService(SERVICE_UUID);
  const rx = await service.getCharacteristic(RX_UUID);
  const tx = await service.getCharacteristic(TX_UUID);

  await tx.startNotifications();

  return { device, server, service, rx, tx };
}
```

**Orden recomendado:**

1. `requestDevice` → `gatt.connect`
2. Obtener servicio y características
3. **`startNotifications()` en TX antes de enviar comandos**
4. Registrar listener de notificaciones
5. Enviar `auth`
6. Resto de comandos con `token`

### 5.2 Desconectar

```javascript
function disconnect(connection) {
  if (connection.device.gatt.connected) {
    connection.device.gatt.disconnect();
  }
}

connection.device.addEventListener('gattserverdisconnected', () => {
  // Limpiar UI, token, buffers de frames, estado WiFi, etc.
  console.log('BLE desconectado');
});
```

### 5.3 Reconexión

Tras desconexión involuntaria:

1. Mostrar «Desconectado».
2. Ofrecer botón «Reconectar» (nuevo gesto de usuario).
3. Repetir flujo completo: conectar → notify → `auth`.

---

## 6. Formato de mensajes

### 6.1 Petición (cliente → Pi, por RX)

```json
{
  "cmd": "nombre_comando",
  "id": "correlacion-opcional",
  "token": "token-opcional",
  "...": "campos del comando"
}
```

| Campo | Descripción |
|-------|-------------|
| `cmd` | **Requerido.** Nombre del comando |
| `id` | Opcional. Correlacionar petición/respuesta en la UI |
| `token` | Requerido en casi todos los comandos tras `auth` |
| Otros campos | Parámetros específicos del comando |

Enviar como bytes UTF-8:

```javascript
async function writeRaw(rx, text) {
  await rx.writeValue(new TextEncoder().encode(text));
}
```

### 6.2 Respuesta (Pi → cliente, por TX notify)

```json
{
  "ok": true,
  "cmd": "nombre_comando",
  "id": "correlacion-opcional",
  "data": { },
  "error": "solo si ok=false"
}
```

- `ok: false` → leer `error` (código estable, a veces `codigo: detalle`).
- `data` contiene el payload de éxito.

---

## 7. Fragmentación BLE (respuestas grandes)

Las notificaciones ATT suelen limitarse a **~512 bytes**. Si la respuesta JSON es mayor, el dispositivo envía **varias notificaciones** con frames de transporte:

```json
{"t":"f","i":0,"n":3,"d":"fragmento del JSON..."}
```

| Campo | Significado |
|-------|-------------|
| `t` | Siempre `"f"` |
| `i` | Índice del frame (0 … n−1) |
| `n` | Total de frames |
| `d` | Fragmento UTF-8 del **JSON de respuesta completo** |

Si la respuesta cabe en una notificación, llega **JSON directo** (sin `t:"f"`).

### 7.1 Reensamblador (JavaScript)

```javascript
class BleResponseAssembler {
  constructor() {
    this.pending = new Map();
    this.timeoutMs = 15000;
  }

  feed(notificationText) {
    const obj = JSON.parse(notificationText);

    if (obj.t !== 'f') {
      return obj; // respuesta final
    }

    const key = `n${obj.n}`; // simplificado; en producción correlacionar por id de petición
    let bucket = this.pending.get(key);
    if (!bucket) {
      bucket = { total: obj.n, parts: new Array(obj.n), timer: null };
      bucket.timer = setTimeout(() => this.pending.delete(key), this.timeoutMs);
      this.pending.set(key, bucket);
    }

    bucket.parts[obj.i] = obj.d;

    if (bucket.parts.every((p) => p !== undefined)) {
      clearTimeout(bucket.timer);
      this.pending.delete(key);
      return JSON.parse(bucket.parts.join(''));
    }

    return null; // aún incompleto
  }
}
```

### 7.2 Cliente comando-respuesta (Promise)

Patrón recomendado para la app:

```javascript
class NiloCardmedClient {
  constructor(rx, tx) {
    this.rx = rx;
    this.tx = tx;
    this.assembler = new BleResponseAssembler();
    this.waiters = new Map(); // id -> { resolve, reject, timer }
    this.token = null;

    tx.addEventListener('characteristicvaluechanged', (event) => {
      const text = new TextDecoder().decode(event.target.value);
      const response = this.assembler.feed(text);
      if (!response) return;

      const id = response.id;
      if (id != null && this.waiters.has(String(id))) {
        const w = this.waiters.get(String(id));
        clearTimeout(w.timer);
        this.waiters.delete(String(id));
        response.ok ? w.resolve(response) : w.reject(response);
      } else {
        // respuesta sin correlación (p.ej. evento push futuro)
        this.onUnhandledResponse?.(response);
      }
    });
  }

  send(payload, timeoutMs = 20000) {
    const id = payload.id ?? String(Date.now());
    payload.id = id;

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.waiters.delete(id);
        reject(new Error('timeout'));
      }, timeoutMs);

      this.waiters.set(id, { resolve, reject, timer });
      writeRaw(this.rx, JSON.stringify(payload)).catch(reject);
    });
  }

  async auth(password) {
    const resp = await this.send({ cmd: 'auth', password });
    this.token = resp.data.token;
    return resp.data;
  }

  async command(cmd, fields = {}) {
    return this.send({ cmd, token: this.token, ...fields });
  }
}
```

---

## 8. Autenticación y comandos privilegiados

### 8.1 Login

**Petición:**

```json
{"cmd":"auth","password":"contraseña-del-dispositivo","id":"1"}
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
    "device_name": "NiloCardmed-d212bd98"
  }
}
```

**Errores:** `invalid_password`

- Guardar `token` y renovar con nuevo `auth` antes de `expires_in` o al recibir `unauthorized`.
- `require_auth` está activo por defecto: solo `auth` funciona sin token.

### 8.2 Comandos privilegiados

Tras `auth`, estos comandos exigen **contraseña otra vez** en la petición **o** sesión elevada (la contraseña en `auth` eleva la sesión durante ~1 h):

| Comando | Alias |
|---------|-------|
| `wifi_connect` | `wifi_configure` |
| `sampling_set_interval` | `set_interval` |
| `sampling_set_window` | `set_monitor_window` |
| `cardmed_configure` | `configure_cardmed`, `configurar` |
| `time_sync` | — |

**Flujo recomendado:** ejecutar siempre `auth` con la contraseña BLE **antes** de `wifi_connect`. Tras un `auth` correcto, la sesión queda elevada ~1 h y en `wifi_connect` el campo `password` es únicamente la **clave WiFi**.

Si la app recibe `privileged_auth_required`, volver a llamar `auth` y repetir el comando.

**Errores:** `unauthorized`, `privileged_auth_required`

---

## 9. Catálogo de comandos

Obtener lista dinámica en runtime:

```json
{"cmd":"commands_list","token":"…","id":"99"}
```

→ `data.commands`: array de strings (incluye alias).

### 9.1 Utilidades

| Comando | Alias | Descripción |
|---------|-------|-------------|
| `auth` | — | Login |
| `ping` | — | Comprueba conexión; `data.pong`, `data.version` |
| `commands_list` | `list_commands` | Lista todos los comandos |

**ping — respuesta:**

```json
{"ok":true,"cmd":"ping","data":{"pong":true,"version":"0.1.0"}}
```

---

### 9.2 WiFi

| Comando | Alias | Descripción |
|---------|-------|-------------|
| `wifi_scan` | — | Lista redes visibles |
| `wifi_connect` | `wifi_configure` | Conectar a SSID |
| `wifi_disconnect` | — | Desconectar |
| `wifi_status` | — | Estado actual |
| `wifi_test` | — | Prueba conectividad HTTP |

#### `wifi_scan`

**Petición:**

```json
{"cmd":"wifi_scan","token":"…","id":"20"}
```

**Respuesta OK:**

```json
{
  "ok": true,
  "cmd": "wifi_scan",
  "data": {
    "networks": [
      {
        "ssid": "MiRed",
        "signal": -52,
        "security": "WPA2",
        "bssid": "AA:BB:…",
        "frequency_mhz": 2437
      }
    ]
  }
}
```

#### `wifi_connect`

**Petición:**

```json
{
  "cmd": "wifi_connect",
  "token": "…",
  "ssid": "MiRed",
  "password": "clave-wifi",
  "persist": true,
  "id": "21"
}
```

| Campo | Descripción |
|-------|-------------|
| `ssid` | **Requerido** |
| `password` | Clave WiFi (red abierta: omitir o vacío) |
| `persist` | Default `true`. Guarda credenciales en el dispositivo |

**Respuesta OK:**

```json
{
  "ok": true,
  "cmd": "wifi_connect",
  "data": {
    "success": true,
    "connected": true,
    "interface": "wlan0",
    "ssid": "MiRed",
    "ip_address": "192.168.1.50",
    "gateway": "192.168.1.1",
    "signal": -48,
    "state": "connected",
    "connectivity_ok": true
  }
}
```

**Errores:** `wifi_connection_failed`, `wifi_error`

**Flujo UI recomendado:**

1. `wifi_scan` → mostrar lista
2. Usuario elige SSID → formulario contraseña
3. `wifi_connect` → mostrar `success` / `connected` / `ip_address`
4. Opcional: `wifi_test` para confirmar Internet

#### `wifi_status`

```json
{"cmd":"wifi_status","token":"…","check_connectivity":true,"id":"22"}
```

#### `wifi_disconnect`

```json
{"cmd":"wifi_disconnect","token":"…","id":"23"}
```

→ `data.success`, `data.connected: false`

---

### 9.3 Batería / alimentación

| Comando | Alias | Descripción |
|---------|-------|-------------|
| `battery_status` | `power_status`, `battery` | Nivel batería vía kernel Linux |

**Petición:**

```json
{"cmd":"battery_status","token":"…","id":"30"}
```

**Respuesta con sensor (UPS / HAT batería):**

```json
{
  "ok": true,
  "cmd": "battery_status",
  "data": {
    "available": true,
    "level_percent": 73,
    "status": "Discharging",
    "primary": {
      "name": "battery",
      "type": "Battery",
      "capacity_percent": 73,
      "status": "Discharging"
    },
    "sources": [ ]
  }
}
```

**Sin sensor** (Pi alimentada por USB / powerbank sin datos al kernel):

```json
{
  "ok": true,
  "data": {
    "available": false,
    "message": "Sin métrica de batería en el kernel (normal con alimentación USB directa o powerbank sin datos)",
    "sources": [],
    "primary": null
  }
}
```

**UI:** Si `available === false`, mostrar «Sin datos de batería» (no es error de conexión).

---

### 9.4 Cámara USB

| Comando | Alias | Descripción |
|---------|-------|-------------|
| `camera_list` | `list_cameras` | Lista dispositivos V4L2 |
| `camera_test` | `test_camera` | Lista + captura en un flujo |
| `camera_capture_test` | `capture_test` | Captura JPEG |
| `camera_capture_chunk` | — | Fragmento de imagen en caché |

#### `camera_list`

```json
{"cmd":"camera_list","token":"…","include_non_capture":false,"id":"40"}
```

**Respuesta:**

```json
{
  "ok": true,
  "data": {
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
}
```

#### `camera_test` (recomendado para la UI)

**Solo listar:**

```json
{"cmd":"camera_test","token":"…","id":"41"}
```

→ `data.cameras[]`, `data.capture: null`, `data.hint`

**Listar + capturar:**

```json
{
  "cmd": "camera_test",
  "token": "…",
  "device": "/dev/video0",
  "mode": "chunked",
  "id": "42"
}
```

→ `data.cameras[]`, `data.device`, `data.capture` (metadatos de captura)

#### Captura e imagen en la tablet

**Paso 1 — iniciar captura (modo `chunked`):**

```json
{
  "cmd": "camera_capture_test",
  "token": "…",
  "device": "/dev/video0",
  "mode": "chunked",
  "id": "43"
}
```

**Respuesta (metadatos):**

```json
{
  "ok": true,
  "data": {
    "mode": "chunked",
    "capture_id": "a1b2c3d4e5f6",
    "device_path": "/dev/video0",
    "size_bytes": 8420,
    "sha256": "…",
    "chunk_size": 200,
    "total_chunks": 43,
    "backend": "fswebcam"
  }
}
```

**Paso 2 — descargar chunks:**

```json
{"cmd":"camera_capture_chunk","token":"…","capture_id":"a1b2c3d4e5f6","index":0,"id":"44-0"}
```

**Respuesta por chunk:**

```json
{
  "ok": true,
  "data": {
    "capture_id": "a1b2c3d4e5f6",
    "index": 0,
    "total_chunks": 43,
    "chunk_size": 200,
    "chunk_base64": "…"
  }
}
```

**Paso 3 — reconstruir JPEG en JavaScript:**

```javascript
function chunksToBlob(chunkResponses) {
  const sorted = chunkResponses.sort((a, b) => a.data.index - b.data.index);
  const bytes = sorted.flatMap((r) => Array.from(atob(r.data.chunk_base64), (c) => c.charCodeAt(0)));
  return new Blob([new Uint8Array(bytes)], { type: 'image/jpeg' });
}
```

Verificar opcionalmente el `sha256` del blob contra `data.sha256` del paso 1.

**Modos alternativos de `camera_capture_test`:**

| mode | Uso |
|------|-----|
| `chunked` | **Recomendado BLE** — metadatos + chunks |
| `base64` | Imagen en una respuesta si cabe (~32 KB máx.) |
| `path` | Solo ruta en disco de la Pi (no útil para tablet) |

**Errores:** `camera_error`, `response_too_large`, `no_capture_available`, `capture_not_found`

---

### 9.5 Muestreo periódico

| Comando | Alias | Descripción |
|---------|-------|-------------|
| `sampling_get` | — | Config actual + ventana temporal |
| `sampling_set_interval` | `set_interval` | Intervalo en segundos (privilegiado) |
| `sampling_set_window` | `set_monitor_window` | Ventana monitorización (privilegiado) |

```json
{"cmd":"sampling_set_interval","token":"…","interval_seconds":120,"id":"50"}
```

```json
{
  "cmd": "sampling_set_window",
  "token": "…",
  "monitor_start": 1700000000,
  "monitor_end": -1,
  "id": "51"
}
```

(`-1` = sin límite)

---

### 9.6 CardMed / servidor SER

| Comando | Alias | Descripción |
|---------|-------|-------------|
| `cardmed_get` | `get_cardmed_config` | Lee configuración |
| `cardmed_configure` | `configure_cardmed`, `configurar` | Guarda config (privilegiado) |
| `cardmed_test` | `probar_cardmed`, `test_cardmed`, `probar` | Prueba E2E |

```json
{
  "cmd": "cardmed_configure",
  "token": "…",
  "site_id": "SITE-001",
  "device_label": "Sala 3",
  "location": "Planta 1",
  "operator_id": "op-42",
  "metadata": {"ward": "cardiology"},
  "id": "60"
}
```

```json
{"cmd":"cardmed_test","token":"…","skip_upload":false,"dry_run":false,"id":"61"}
```

→ `data.success`, `data.steps[]` con detalle por paso (WiFi, captura, SER, etc.)

---

### 9.7 Salud, sistema y telemetría

| Comando | Alias | Descripción |
|---------|-------|-------------|
| `health_status` | `health`, `system_health` | Informe salud global |
| `system_info` | — | Versión, uptime, disco, RAM, power |
| `storage_status` | — | Espacio disco, cola pending |
| `sampler_history` | — | Últimos ciclos muestreo |
| `events_list` | — | Eventos recientes |
| `time_get` | — | Hora UTC del dispositivo |
| `time_sync` | — | Ajustar hora (privilegiado) |

#### `health_status`

```json
{"cmd":"health_status","token":"…","id":"70"}
```

**Respuesta (estructura):**

```json
{
  "ok": true,
  "data": {
    "version": "0.1.0",
    "healthy": false,
    "degraded": true,
    "status": "degraded",
    "components": [
      {"name": "wifi", "ok": true, "message": "…", "severity": "healthy"},
      {"name": "camera", "ok": false, "message": "…", "severity": "degraded"}
    ]
  }
}
```

Interpretación UI:

| `status` | Significado |
|----------|-------------|
| `healthy` | Todo nominal |
| `degraded` | Operativo con carencias (sin WiFi, sin cámara…) |
| `unhealthy` | Fallo grave |

---

## 10. Flujos de pantalla sugeridos

### 10.1 Onboarding dispositivo

```text
[Conectar BLE] → auth → health_status → (opcional) system_info / battery_status
```

### 10.2 Configurar WiFi

```text
wifi_scan → usuario elige SSID → wifi_connect → wifi_test → wifi_status
```

### 10.3 Probar cámara

```text
camera_list → usuario elige cámara → camera_capture_test (chunked)
→ bucle camera_capture_chunk → mostrar JPEG
```

Atajo: un solo `camera_test` con `device` + `mode: chunked`.

### 10.4 Dashboard estado

```text
health_status + battery_status + wifi_status + storage_status
```

---

## 11. Tabla de errores

| Código `error` | Acción en la app |
|----------------|------------------|
| `unauthorized` | Volver a `auth` |
| `invalid_password` | Mostrar error al operador |
| `privileged_auth_required` | Pedir contraseña BLE de nuevo |
| `wifi_error` | Error genérico WiFi |
| `wifi_connection_failed` | SSID/clave incorrectos o sin conectividad |
| `camera_error` | Cámara no disponible / fallo captura |
| `response_too_large` | Usar `mode: chunked` |
| `no_capture_available` | Ejecutar captura antes de pedir chunk |
| `capture_not_found` | `capture_id` incorrecto o expirado |
| `invalid_parameter` | Revisar campos de la petición |
| `comando no soportado: …` | Actualizar app o firmware |
| `time_sync_failed` | Reintentar / permisos hora |

Formato ocasional: `"codigo: detalle"`.

---

## 12. Límites y tiempos

| Parámetro | Valor típico | Notas |
|-----------|--------------|-------|
| Token TTL | 3600 s | Renovar con `auth` |
| Sesión privilegiada | 3600 s | Tras `auth` con contraseña correcta |
| Max mensaje RX | 512 B | Peticiones pequeñas |
| Max respuesta JSON | 4096 B | Comandos normales |
| Max imagen base64 | 32768 B | Modo base64 |
| Frame payload | 200 B | Fragmentación BLE |
| Chunk imagen | 200 B | `camera_capture_chunk` |
| Delay entre frames | 15 ms | En el dispositivo |

**Timeouts recomendados en la app:**

| Operación | Timeout |
|-----------|---------|
| Comando normal | 20 s |
| `wifi_scan` | 30 s |
| `wifi_connect` | 60 s |
| Descarga imagen (todos los chunks) | 120 s |

---

## 13. Ejemplo mínimo completo (JavaScript)

```javascript
const SERVICE_UUID = '6e400010-b5a3-f393-e0a9-e50e24dcca9e';
const RX_UUID = '6e400011-b5a3-f393-e0a9-e50e24dcca9e';
const TX_UUID = '6e400012-b5a3-f393-e0a9-e50e24dcca9e';

async function demo(password) {
  const device = await navigator.bluetooth.requestDevice({
    filters: [{ namePrefix: 'NiloCardmed' }],
    optionalServices: [SERVICE_UUID],
  });

  const server = await device.gatt.connect();
  const service = await server.getPrimaryService(SERVICE_UUID);
  const rx = await service.getCharacteristic(RX_UUID);
  const tx = await service.getCharacteristic(TX_UUID);

  const client = new NiloCardmedClient(rx, tx);
  await tx.startNotifications();

  await client.auth(password);

  const health = await client.command('health_status', { id: 'h1' });
  console.log('health', health.data.status);

  const scan = await client.command('wifi_scan', { id: 'w1' });
  console.log('redes', scan.data.networks);

  const battery = await client.command('battery_status', { id: 'b1' });
  console.log('batería', battery.data.level_percent ?? 'N/A');

  device.gatt.disconnect();
}
```

*(Requiere la clase `NiloCardmedClient` de la sección 7.2.)*

---

## 14. Checklist de integración

- [ ] HTTPS en producción
- [ ] Botón explícito para `requestDevice`
- [ ] Filtro `namePrefix: 'NiloCardmed'`
- [ ] Notify TX antes de comandos
- [ ] Reensamblador de frames `{t:"f"}`
- [ ] Correlación por `id` petición/respuesta
- [ ] Manejo `unauthorized` → re-`auth`
- [ ] WiFi: scan → connect → mostrar `success`
- [ ] Cámara: chunked + bucle de chunks
- [ ] Batería: manejar `available: false`
- [ ] Listener `gattserverdisconnected`
- [ ] Timeouts por tipo de operación

---

## 15. Versión

Este documento corresponde al protocolo implementado en **NiloCardmed 0.1.x** (comandos registrados en `nilocardmed/bluetooth/handlers.py`).

Para comprobar comandos en un dispositivo concreto: `commands_list` tras `auth`.
