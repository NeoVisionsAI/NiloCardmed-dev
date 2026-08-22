# Briefing Frontend — Panel de aprovisionamiento WiFi (UI v2)

**Para:** agente/desarrollador Frontend  
**De:** equipo Backend / dispositivo NiloCardmed  
**Base URL (solo en AP):** `http://192.168.4.1:8080`  
**Contrato completo:** `docs/BLUETOOTH_PROTOCOL.md` · `docs/Integracion_Frontend_WiFi.md`

---

## 1. Resumen del flujo UX solicitado

1. Pantalla «Emparejar» → usuario conecta tablet al AP `Nilocardmed-Config-xxxx` → **Comprobar conexión**.
2. Si OK → **navegar a VISTA de configuración** (no quedarse en la misma pantalla).
3. **VISTA** empieza por **login** (contraseña = misma que WPA del AP = `NILOCARDMED_CONNECTION_PASSWORD`).
4. Tras login → **pestañas**: **Estado** (default) · WiFi · Cámara · CardMed. **Eliminar pestaña Sistema**.

---

## 2. Cambios de UI (frontend)

### 2.1 Login

| Cambio | Detalle |
|--------|---------|
| Layout | Input + botón **en la misma fila** (botón a la derecha) |
| Tamaño | Input más compacto (no full-width gigante) |
| Hint | **Quitar** placeholder/hint del input |
| Ver contraseña | Toggle show/hide |
| Espaciado | Separar visualmente label, campo y mensajes de error |

### 2.2 Pestaña **Estado** (primera por defecto)

Tarjetas visuales con botón **Refrescar** (re-fetch del panel).

| Dato | Fuente API |
|------|------------|
| WiFi conectada (SSID, IP, señal) | `GET /api/dashboard` → `wifi` o comando `device_status` |
| Batería / alimentación | `power.display_percent`, `power.source_label`, `power.power_source` |
| Intervalo sampling | `sampling.interval_seconds` |
| Cámara conectada | `camera.connected`, `camera.cameras_count`, `camera.saved_device_present` |
| Última config guardada | `config_last_saved_at` (ISO8601 UTC) |
| Imágenes tomadas | `captures.cycles_successful` (+ opcional `captures.images_on_disk`) |

**Presentación batería (backend ya normaliza):**

| `power.power_source` | UI sugerida |
|----------------------|-------------|
| `mains` | «Corriente» + **100 %** |
| `usb` | «USB / alimentación externa» + **100 %** |
| `powerbank` | «Powerbank / batería» + `display_percent` % |
| `unknown` | Mensaje genérico |

### 2.3 Pestaña **WiFi**

- Escaneo: `wifi_scan`
- Conectar: `wifi_connect` con `{ ssid, password, persist: true }`
- Layout más compacto: SSID, contraseña y botón en fila o grid, no inputs full-width apilados

### 2.4 Pestaña **Cámara** (nueva)

| Acción | API |
|--------|-----|
| Listar cámaras | `camera_list` |
| Seleccionar cámara activa (persiste en Pi) | `camera_set_device` `{ device: "/dev/video0" }` |
| Leer selección guardada | `camera_get_device` |
| Foto de prueba | `camera_capture_test` `{ device, mode: "base64" }` |
| Mostrar imagen | `data.image_base64` → `<img src="data:image/jpeg;base64,...">` |

> Usar **`mode: "base64"`** por HTTP (no chunked; en WiFi local cabe bien).

### 2.5 Pestaña **CardMed**

| Acción | API |
|--------|-----|
| Leer config actual | `cardmed_get` |
| Config manual (código texto) | `cardmed_configure` `{ config_code: "SITE\|Sala\|op\|loc" }` o JSON |
| Config por JSON | `cardmed_configure` `{ config_json: "{...}" }` |
| Config por QR (cámara del Pi) | `cardmed_scan_qr` `{ apply: true }` — usa cámara guardada |
| Probar (captura + pipeline) | `cardmed_test` `{ skip_upload: true }` de momento |

**QR:** el frontend dispara `cardmed_scan_qr`; el Pi captura con la cámara previamente guardada (`camera_set_device`), decodifica QR y aplica config.

**Botón «Probar» (fase actual):** llamar `cardmed_test`. Devuelve pasos (`steps[]`), captura y resultado JSON. El servicio externo de algoritmo **se definirá después** — dejar hook/UI preparada para mostrar `steps` y respuesta.

### 2.6 Eliminar

- Pestaña **Sistema** (sobra en esta fase)
- Código BLE oculto por flag, **no borrar**

---

## 3. API HTTP — referencia rápida

### Sin autenticación

| Método | Ruta | Uso |
|--------|------|-----|
| GET | `/api/status` | Health check antes de entrar a VISTA |
| GET | `/api/dashboard` | **Panel Estado completo** (ideal para Refrescar) |
| GET | `/api/config` | Resumen estático (legacy; preferir dashboard) |

### Autenticación

```javascript
const res = await fetch(`${DEVICE_API}/api/command`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    cmd: 'auth',
    payload: { password: connectionPassword },
  }),
});
const { data } = await res.json();
const token = data.token;
```

Comandos siguientes: header `Authorization: Bearer ${token}`.

### Comandos nuevos / relevantes para esta UI

| Comando | Auth | Payload ejemplo |
|---------|------|-----------------|
| `device_status` | Token | `{}` — mismo cuerpo que `/api/dashboard` |
| `camera_get_device` | Token | `{}` |
| `camera_set_device` | Token + privilegiado | `{ "device": "/dev/video0" }` |
| `camera_list` | Token | `{}` |
| `camera_capture_test` | Token | `{ "device": "/dev/video0", "mode": "base64" }` |
| `wifi_scan` | Token | `{ "rescan": true }` |
| `wifi_connect` | Token + privilegiado | `{ "ssid": "...", "password": "...", "persist": true }` |
| `wifi_status` | Token | `{ "check_connectivity": true }` |
| `cardmed_get` | Token | `{}` |
| `cardmed_configure` | Token + privilegiado | `{ "config_code": "SITE\|Sala\|op" }` |
| `cardmed_scan_qr` | Token + privilegiado | `{ "apply": true }` |
| `cardmed_test` | Token | `{ "skip_upload": true }` |

---

## 4. Ejemplo `GET /api/dashboard`

```json
{
  "device_name": "NiloCardmed-a1b2c3d4",
  "version": "0.1.0",
  "wifi": {
    "connected": true,
    "ssid": "MiniPC-WiFi",
    "ip_address": "192.168.1.50",
    "signal": -52,
    "connectivity_ok": true
  },
  "power": {
    "power_source": "mains",
    "source_label": "Corriente",
    "display_percent": 100,
    "on_battery": false
  },
  "sampling": {
    "enabled": true,
    "interval_seconds": 120,
    "window_active": true,
    "window_reason": "inside_window"
  },
  "camera": {
    "connected": true,
    "saved_device": "/dev/video0",
    "saved_device_present": true,
    "cameras_count": 1,
    "cameras": [{ "path": "/dev/video0", "name": "USB Camera" }]
  },
  "captures": {
    "cycles_successful": 42,
    "cycles_recorded": 45,
    "images_on_disk": 3,
    "last_capture_success_at": "2026-08-22T18:30:00+00:00"
  },
  "cardmed": {
    "enabled": true,
    "site_id": "SITE-001",
    "configured": true
  },
  "config_last_saved_at": "2026-08-22T17:00:00+00:00",
  "refreshed_at": "2026-08-22T18:45:00+00:00"
}
```

---

## 5. Feature flag

```javascript
export const PROVISIONING_MODE = 'wifi'; // 'wifi' | 'bluetooth'
```

---

## 6. Checklist entrega frontend

- [ ] Comprobar conexión → navega a VISTA
- [ ] Login mejorado (layout, show password)
- [ ] Pestañas: Estado · WiFi · Cámara · CardMed (sin Sistema)
- [ ] Estado: dashboard + botón Refrescar
- [ ] WiFi: layout compacto + scan/connect
- [ ] Cámara: listar, seleccionar, probar foto base64
- [ ] CardMed: código manual, QR vía `cardmed_scan_qr`, botón Probar
- [ ] Errores de red / timeout 5–10 s
- [ ] Probar en tablet real en AP `Nilocardmed-Config-xxxx`

---

## 7. Despliegue backend (Pi)

Tras cambios en dispositivo:

```bash
cd ~/dev/NiloCardmed-dev && sudo ./scripts/update.sh
# Si hay cambios Docker (zbar-tools): sudo ./scripts/update.sh --build
```
