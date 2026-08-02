# Cliente Web Bluetooth (Android)

Guía para integrar una **app web / PWA en tablet Android** con NiloCardmed-dev vía **Web Bluetooth API**.

Requisitos del dispositivo NiloCardmed: ver [BLUETOOTH_PROTOCOL.md](BLUETOOTH_PROTOCOL.md).

## Compatibilidad

| Plataforma | Navegador | Soporte |
|------------|-----------|---------|
| Android tablet | Chrome, Edge, Samsung Internet | Web Bluetooth nativo |
| iOS / iPad | Safari | No soportado nativamente (futuro: otro transporte) |

La app web debe servirse por **HTTPS** (o `http://localhost` en desarrollo). La conexión BLE requiere un **gesto del usuario** (tap en botón).

## UUIDs por defecto

```javascript
const SERVICE_UUID = '6e400010-b5a3-f393-e0a9-e50e24dcca9e';
const RX_UUID = '6e400011-b5a3-f393-e0a9-e50e24dcca9e'; // write
const TX_UUID = '6e400012-b5a3-f393-e0a9-e50e24dcca9e'; // read + notify
const DEVICE_NAME = 'NiloCardmed'; // configurable en la Pi
```

Parametrizables con `NILOCARDMED_BLUETOOTH__*`.

## Flujo de conexión

```javascript
async function connectNiloCardmed() {
  const device = await navigator.bluetooth.requestDevice({
    filters: [{ name: DEVICE_NAME }],
    optionalServices: [SERVICE_UUID],
  });

  const server = await device.gatt.connect();
  const service = await server.getPrimaryService(SERVICE_UUID);
  const rx = await service.getCharacteristic(RX_UUID);
  const tx = await service.getCharacteristic(TX_UUID);

  await tx.startNotifications();
  tx.addEventListener('characteristicvaluechanged', onTxNotification);

  return { device, server, rx, tx };
}
```

Recomendado:

1. Conectar GATT.
2. Suscribirse a notificaciones TX **antes** de enviar comandos.
3. `auth` → guardar `token` y `expires_in`.
4. Resto de comandos con `"token": "…"`.
5. Manejar desconexión (`device.addEventListener('gattserverdisconnected', …)`).

## Enviar comandos (RX)

```javascript
async function sendCommand(rx, payload) {
  const bytes = new TextEncoder().encode(JSON.stringify(payload));
  await rx.writeValue(bytes);
}
```

Para peticiones grandes (futuro CardMed), fragmentar con el mismo formato de frames que las respuestas (ver abajo).

## Recibir respuestas (TX)

NiloCardmed puede enviar **varias notificaciones** por comando si la respuesta JSON supera el MTU BLE (~512 bytes).

### Respuesta directa (1 frame)

JSON estándar del protocolo:

```json
{"ok":true,"cmd":"ping","id":"2","data":{"pong":true,"version":"0.1.0"}}
```

### Respuesta fragmentada (N frames)

Cada notificación:

```json
{"t":"f","i":0,"n":3,"d":"fragmento UTF-8 del JSON completo"}
```

| Campo | Significado |
|-------|-------------|
| `t` | Siempre `"f"` (frame de transporte) |
| `i` | Índice del frame (0 … n-1) |
| `n` | Total de frames |
| `d` | Fragmento de texto del JSON de respuesta |

### Implementación recomendada en JavaScript

```javascript
const pendingFrames = new Map(); // id lógico o por orden de llegada

function onTxNotification(event) {
  const text = new TextDecoder().decode(event.target.value);
  const obj = JSON.parse(text);

  if (obj.t !== 'f') {
    handleResponse(obj);
    return;
  }

  const key = `seq-${obj.n}`; // o correlacionar por cmd/id si lo incluyes en el futuro
  let bucket = pendingFrames.get(key);
  if (!bucket) {
    bucket = { total: obj.n, parts: new Array(obj.n) };
    pendingFrames.set(key, bucket);
  }
  bucket.parts[obj.i] = obj.d;

  if (bucket.parts.every((p) => p !== undefined)) {
    pendingFrames.delete(key);
    const fullJson = bucket.parts.join('');
    handleResponse(JSON.parse(fullJson));
  }
}
```

Si tras un timeout no llegan todos los frames, reintentar el comando o hacer `readValue()` en TX como fallback (contiene la última respuesta reensamblada en la Pi).

## Secuencia típica de la app

```javascript
// 1. Conectar (botón "Conectar dispositivo")
const { rx, tx } = await connectNiloCardmed();

// 2. Autenticar
await sendCommand(rx, { cmd: 'auth', password: 'changeme', id: '1' });
// → en handleResponse: guardar token

// 3. Ping
await sendCommand(rx, { cmd: 'ping', token, id: '2' });

// 4. WiFi
await sendCommand(rx, { cmd: 'wifi_scan', token, id: '3' });
await sendCommand(rx, {
  cmd: 'wifi_connect',
  token,
  ssid: 'MiRed',
  password: 'secreto',
  persist: true,
  id: '4',
});

// 5. Muestreo
await sendCommand(rx, {
  cmd: 'sampling_set_interval',
  token,
  interval_seconds: 120,
  id: '5',
});

// 6. Imagen de prueba (modo chunked)
await sendCommand(rx, {
  cmd: 'camera_capture_test',
  token,
  mode: 'chunked',
  id: '6',
});
// → data: { capture_id, total_chunks, sha256, … }

for (let i = 0; i < totalChunks; i++) {
  await sendCommand(rx, {
    cmd: 'camera_capture_chunk',
    token,
    capture_id,
    index: i,
    id: `chunk-${i}`,
  });
  // Cada respuesta puede llegar en varios frames BLE; reensamblar antes de parsear
}

// 7. Configurar CardMed
await sendCommand(rx, {
  cmd: 'cardmed_configure',
  token,
  site_id: 'SITE-001',
  device_label: 'Sala 3',
  operator_id: 'op-42',
  metadata: { ward: 'cardiology' },
  id: '7',
});

// 8. Probar CardMed (captura + validación; skip_upload en desarrollo)
const testResp = await sendCommand(rx, {
  cmd: 'cardmed_test',
  token,
  skip_upload: true,
  id: '8',
});
// testResp.data.success === true y testResp.data.steps[] con feedback por paso
```

## Errores habituales

| `error` | Acción en la app |
|---------|------------------|
| `unauthorized` | Volver a `auth` |
| `invalid_password` | Mostrar error al operador |
| `wifi_error` / `wifi_connection_failed` | Mostrar SSID/red y reintentar |
| `camera_error` | Comprobar cámara USB en la Pi |
| `response_too_large` | Usar `mode: 'chunked'` en captura |

## Capa de transporte abstracta (futuro iOS)

Encapsula BLE en un módulo para poder sustituirlo:

```javascript
class BleTransport {
  async connect() { /* … */ }
  async send(payload) { /* write + await reensamblado notify */ }
  disconnect() { /* … */ }
}

// Futuro: HttpTransport apuntando a API local WiFi
```

El contrato de comandos JSON (`cmd`, `token`, `data`, `ok`, `error`) permanece igual.

## Pruebas sin tablet

En la Pi o en desarrollo:

```bash
NILOCARDMED_DATA_DIR=/tmp/nilocardmed-test \
  python -m nilocardmed.main bluetooth test-framing

NILOCARDMED_DATA_DIR=/tmp/nilocardmed-test \
  python -m nilocardmed.main bluetooth test-commands --skip-camera-capture
```

## Parámetros BLE relevantes (Pi)

| Variable | Default | Uso |
|----------|---------|-----|
| `BLE_FRAMING_ENABLED` | `true` | Fragmentación automática |
| `BLE_MAX_NOTIFICATION_BYTES` | `512` | Tope por notificación |
| `BLE_FRAME_PAYLOAD_BYTES` | `200` | Bytes JSON por frame |
| `CAPTURE_CHUNK_SIZE` | `200` | Tamaño lógico de imagen por chunk |
| `BLE_INTER_FRAME_DELAY_MS` | `15` | Pausa entre notifies consecutivos |

Si la tablet pierde frames, reduce `BLE_FRAME_PAYLOAD_BYTES` o aumenta ligeramente el delay.
