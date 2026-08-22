# Briefing para agente Frontend — Emparejamiento WiFi NiloCardmed

**Para:** agente/desarrollador Frontend  
**De:** equipo Backend / dispositivo NiloCardmed  
**Objetivo:** sustituir el flujo Web Bluetooth por aprovisionamiento vía WiFi AP local.  
**Importante:** no eliminar código Bluetooth del frontend si existe; desactivarlo/ocultarlo por feature flag. El backend conserva BLE por si hace falta reactivarlo.

---

## 1. Resumen ejecutivo

El dispositivo (Raspberry Pi Zero 2 W) expone un **punto de acceso WiFi permanente** para configuración local. La tablet debe unirse temporalmente a esa red; la app web comprueba conectividad con HTTP y muestra el panel de configuración.

| Antes (BLE) | Ahora (WiFi AP) |
|-------------|-----------------|
| `navigator.bluetooth.requestDevice` | Usuario conecta WiFi `Nilocardmed-Config-xxxx` |
| Listado / filtro por nombre BLE | **Eliminar de la UI** (no hace falta listar dispositivos) |
| GATT + JSON por características | HTTP JSON en `http://192.168.4.1:8080` |
| Contraseña BLE | `NILOCARDMED_CONNECTION_PASSWORD` (comando `auth`) |

---

## 2. Qué quitar u ocultar en el frontend (sin borrar código)

Desactivar u ocultar detrás de flag (p. ej. `USE_WIFI_PROVISIONING = true`):

- Pantallas de **escaneo / listado Bluetooth** (`requestDevice`, filtros `namePrefix: 'NiloCardmed'`, etc.)
- Flujo **emparejar en Ajustes → Bluetooth** del SO
- Indicadores de **conexión GATT** / notify / framing BLE
- Cualquier dependencia de **Web Bluetooth API**

**Conservar el código** comentado o detrás del flag por si se reactiva BLE en el futuro.

---

## 3. Nueva vista: «Emparejar con Nilocardmed»

### 3.1 Estados de la vista

1. **`idle`** — Instrucciones para cambiar WiFi (tarjeta vacía / placeholder).
2. **`checking`** — Tras pulsar «Comprobar conexión».
3. **`connected`** — `GET /api/status` OK → mostrar panel.
4. **`unreachable`** — Sin respuesta → mensaje de error claro.

### 3.2 Copy sugerido (idle)

> Para configurar este Nilocardmed, conecta la tablet a la red WiFi **`Nilocardmed-Config-xxxx`** (xxxx = últimos 4 caracteres de la MAC; visible en la lista WiFi del dispositivo).  
> Luego vuelve aquí y pulsa **Comprobar conexión**.

### 3.3 Comprobar conexión

```javascript
const DEVICE_API = 'http://192.168.4.1:8080';

async function checkDeviceReachable() {
  try {
    const res = await fetch(`${DEVICE_API}/api/status`, { cache: 'no-store' });
    if (!res.ok) return false;
    const data = await res.json();
    return data.status === 'ok';
  } catch {
    return false;
  }
}
```

Respuesta esperada:

```json
{ "status": "ok", "device": "Nilocardmed", "device_name": "NiloCardmed-a1b2c3d4", "version": "0.x.x" }
```

### 3.4 Panel de configuración (connected)

**Opción A — iframe (mínimo esfuerzo):**

```html
<iframe src="http://192.168.4.1:8080/" title="Configuración NiloCardmed" />
```

**Opción B — UI nativa en la PWA** usando la API (recomendado para UX unificada).

### 3.5 Cierre

Tras guardar: indicar al usuario que **vuelva a la WiFi con internet** (MiniPC / oficina). Fuera del AP no se puede alterar la configuración del dispositivo.

---

## 4. API HTTP local (misma semántica que BLE)

Base: **`http://192.168.4.1:8080`** (solo accesible en la red AP).

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| GET | `/api/status` | No | Health check |
| GET | `/api/config` | No | Resumen config (sin secretos) |
| POST | `/api/command` | Según comando | Protocolo JSON idéntico a BLE |
| GET | `/` | No | HTML de configuración (PoC) |

### Autenticación

Contraseña definida en dispositivo como **`NILOCARDMED_CONNECTION_PASSWORD`** (la conoce el operador / se configuró en fábrica).

```javascript
// 1) Auth
const auth = await fetch(`${DEVICE_API}/api/command`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    cmd: 'auth',
    payload: { password: connectionPassword },
  }),
});
const { data } = await auth.json();
const token = data.token;

// 2) Comandos con token
await fetch(`${DEVICE_API}/api/command`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  },
  body: JSON.stringify({
    cmd: 'wifi_connect',
    payload: { ssid: 'MiniPC-WiFi', password: '...' },
  }),
});
```

Comandos útiles: `auth`, `ping`, `wifi_scan`, `wifi_status`, `wifi_connect`, `cardmed_get`, `cardmed_configure`, `health_status`.  
Contrato completo (legacy BLE): `docs/BLUETOOTH_PROTOCOL.md` en repo backend.

### CORS

El servidor responde con `Access-Control-Allow-Origin: *` para permitir `fetch` desde la PWA (HTTPS) mientras la tablet está en el AP.

---

## 5. Consideraciones UX / técnicas

1. **Sin internet en el AP** — La PWA puede seguir en memoria pero no recargará assets externos; preparar UI offline o iframe al Pi.
2. **No hacer fetch a 192.168.4.1** hasta que el usuario haya cambiado de red WiFi.
3. **Timeout recomendado** — 5–10 s en `checkDeviceReachable`.
4. **No usar Web Bluetooth** en este flujo; en iOS tampoco está disponible.
5. **Seguridad por presencia física** — Solo quien está al lado del Pi puede unirse al AP abierto en subred aislada `192.168.4.0/24`.

---

## 6. Feature flag sugerido en frontend

```javascript
export const PROVISIONING_MODE = 'wifi'; // 'wifi' | 'bluetooth' (legacy)

export function isWifiProvisioning() {
  return PROVISIONING_MODE === 'wifi';
}
```

- `wifi` → nueva vista descrita arriba.  
- `bluetooth` → flujo antiguo (solo rescate; no usar en producción actual).

---

## 7. Documentación de referencia (repo backend)

- **`docs/Integracion_Frontend_WiFi.md`** — Especificación detallada + diagramas
- **`docs/BLUETOOTH_PROTOCOL.md`** — Comandos JSON (válidos también por HTTP)
- **`docs/Integracion_Frontend.md`** — Legacy Web Bluetooth (no usar en prod)

---

## 8. Checklist de entrega frontend

- [ ] Ocultar/desactivar UI Bluetooth (sin borrar código)
- [ ] Vista «Emparejar con Nilocardmed» con estados idle / checking / connected / unreachable
- [ ] Botón «Comprobar conexión» → `GET /api/status`
- [ ] Panel config (iframe o API + formularios)
- [ ] Flujo `auth` → token → comandos (`wifi_connect`, etc.)
- [ ] Mensaje de retorno a WiFi con internet
- [ ] Manejo de errores de red / timeout
- [ ] Probar en tablet Android real conectada al AP `Nilocardmed-Config-xxxx`

---

## 9. Verificación en dispositivo (para QA conjunto)

Desde tablet en el AP del Pi:

```bash
curl -s http://192.168.4.1:8080/api/status
```

Debe responder `{"status":"ok",...}`. Bluetooth estará apagado en la Pi (ahorro memoria); es comportamiento esperado.
