# Briefing Backend/Pi — Replicar GUI de aprovisionamiento en el dispositivo

**Para:** agente/desarrollador del **servicio HTTP en la Raspberry Pi** (NiloCardmed)  
**De:** equipo Frontend (NILO-frontend)  
**Fecha:** 2026-08-23  
**Objetivo:** servir en el Pi la **misma GUI de configuración** que hoy vive en la PWA React, para que la tablet solo haga «Comprobar conexión» y abra una pestaña al Pi.

---

## 1. Cambio arquitectónico

### Situación actual

| Pieza | Dónde está | Qué hace |
|-------|------------|----------|
| Botón «Comprobar conexión» | PWA NILO (tablet, HTTPS) | `GET http://192.168.4.1:8080/api/status` |
| GUI de configuración (login + pestañas) | PWA NILO (React embebido) | Llama a la API del Pi desde el navegador |
| API REST | Pi `:8080` | `/api/status`, `/api/dashboard`, `/api/command` |

**Problema:** la GUI corre en la PWA (servidor externo). En el AP del Pi **no hay internet**, así que recargar la PWA o cargar assets externos puede fallar.

### Situación objetivo

| Pieza | Dónde estará |
|-------|--------------|
| «Comprobar conexión» | Sigue en la PWA tablet (mínimo) |
| **GUI completa de configuración** | **Servida por el Pi** en `GET /` |
| API REST | Pi (sin cambios de contrato) |

### Flujo UX final

```
Tablet (PWA NILO)                         Pi (192.168.4.1:8080)
─────────────────                         ───────────────────────
1. Usuario conecta WiFi AP
   Nilocardmed-Config-xxxx
2. Pulsa «Comprobar conexión」
   → GET /api/status
3. Si OK → abre NUEVA PESTAÑA ──────────→ GET /
                                          Login + pestañas
                                          (misma origin → /api/*)
4. Usuario configura WiFi/cámara/CardMed
5. Cierra pestaña, vuelve a WiFi con internet
```

**Integración en tablet (referencia para agente Frontend):**

```javascript
const DEVICE_GUI = 'http://192.168.4.1:8080/';

async function onCheckConnection() {
  const ok = await checkDeviceReachable(); // GET /api/status
  if (ok) {
    window.open(DEVICE_GUI, '_blank', 'noopener,noreferrer');
  }
}
```

> La PWA **no** debe seguir mostrando la vista de configuración embebida; solo el emparejamiento + apertura de pestaña.

---

## 2. Qué hay que implementar en el Pi

### Incluir (paridad con la GUI actual)

Implementar **solo la vista de configuración** (`CardmedWifiConfigView`), **no** la pantalla de emparejar (`CardmedWifiPairView` — eso sigue en la tablet).

Contenido:

1. **Login** (contraseña del dispositivo)
2. **Pestañas:** Estado · WiFi · Cámara · CardMed
3. Pantalla **«Configuración completada»** (botón Finalizar)
4. Misma lógica de API, timeouts y mensajes de error

### No incluir

- Pantalla «Emparejar con Nilocardmed» (idle/checking/unreachable del pair)
- Pestaña **Sistema** (eliminada a propósito)
- Código Web Bluetooth / BLE

---

## 3. URL y despliegue en el Pi

| Ruta | Contenido |
|------|-----------|
| `GET /` | SPA/HTML de configuración (esta GUI) |
| `GET /api/status` | Ya existe |
| `GET /api/dashboard` | Ya existe |
| `POST /api/command` | Ya existe |

**Base URL de la API desde la GUI del Pi:** usar **same-origin**:

```javascript
const API_BASE = window.location.origin; // p.ej. http://192.168.4.1:8080
// fetch(`${API_BASE}/api/dashboard`)
// fetch(`${API_BASE}/api/command`, ...)
```

Ventajas: sin CORS, sin hardcodear IP, funciona aunque cambie el puerto.

**Opciones de implementación (elige una):**

| Opción | Descripción |
|--------|-------------|
| **A — Static build** | Compilar la carpeta React de NILO-frontend como SPA estática y servir `dist/` en `/` |
| **B — HTML+JS vanilla** | Reimplementar según esta spec (sin React) |
| **C — Template server-side** | Jinja/Go templates + fetch; más trabajo, mismo resultado visual |

Referencia canónica del código React: ver sección 10.

---

## 4. Mapa de pantallas y estados

### 4.1 Vista principal (`CardmedWifiConfigView`)

```
┌─────────────────────────────────────────────────────────────┐
│ [← Emparejar]   Configuración Nilocardmed          [Finalizar]│
│                 NiloCardmed-a1b2c3d4                          │
├─────────────────────────────────────────────────────────────┤
│  SI NO autenticado:                                           │
│  ┌─ Login ────────────────────────────────────────────────┐  │
│  │ 🔒 Acceso al dispositivo                                │  │
│  │ NiloCardmed-xxx · v0.1.0                                │  │
│  │ Hint: misma contraseña que la red WiFi del Pi           │  │
│  │ Contraseña  [__________][👁]  [ Entrar ]                │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                               │
│  SI autenticado:                                              │
│  [Estado] [WiFi] [Cámara] [CardMed]   ← pestañas pill        │
│  ┌─ panel activo ──────────────────────────────────────────┐  │
│  │  (contenido de la pestaña)                               │  │
│  └─────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Estados globales:**

| Estado | Comportamiento |
|--------|----------------|
| `!authenticated` | Solo login |
| `authenticated + tab` | Pestañas visibles |
| `configDone` | Pantalla «Configuración completada» |

**Botón «Emparejar» (header):** en la GUI del Pi puede ser un enlace `javascript:window.close()` o simplemente ocultarse — el usuario llega desde la tablet.

**Botón «Finalizar»:** muestra pantalla done con copy «Vuelve a la WiFi con internet…».

---

## 5. Detalle por pantalla

### 5.1 Login (`CardmedWifiLoginForm`)

| Elemento | Detalle |
|----------|---------|
| Título | «Acceso al dispositivo» |
| Subtítulo | `{device_name}` + `v{version}` (de `/api/status` previo o tras auth) |
| Hint | «Usa la misma contraseña que la red WiFi del Pi.» |
| Campo | Input password + **toggle ojo** (`visibility` / `visibility_off`) |
| Botón | **Entrar** (primario, misma fila que el input) |
| Error | Texto rojo bajo el formulario |

**API:**

```http
POST /api/command
Content-Type: application/json

{
  "cmd": "auth",
  "id": "173…",
  "payload": { "password": "…" }
}
```

Respuesta: `data.token` → guardar en memoria (sessionStorage opcional).  
Header siguientes: `Authorization: Bearer {token}`.

**Layout CSS clave:**

- Input wrap: borde redondeado 12px, altura 44px
- Fila horizontal: `[input+ojo] [Entrar]`
- En móvil: columna (input arriba, botón full-width)

---

### 5.2 Pestaña **Estado** (default, `CardmedWifiStatusTab`)

**Toolbar:**

- Botón primario **Refrescar** + icono `refresh`
- Meta: «Actualizado: {fecha}» o «Sin datos cargados — pulsa Refrescar»

**Comportamiento crítico:**

- Las **6 tarjetas siempre visibles**, aunque falle el dashboard
- Valores sin datos: **`—`** (em dash)
- Error de red/API: **banner rojo arriba**, no ocultar tarjetas
- Al refrescar con error: **no borrar** datos previos del dashboard

**API:**

```javascript
// 1) Preferido
GET /api/dashboard   // timeout 10 s

// 2) Fallback si falla GET y hay token
POST /api/command  { cmd: "device_status", payload: {} }
```

**6 tarjetas (grid responsive, min 220px):**

| Tarjeta | Icono | Campos |
|---------|-------|--------|
| WiFi | `wifi` | Activo, SSID, IP, Señal (dBm), Internet |
| Alimentación | `battery_charging_full` | Fuente, Nivel |
| Muestreo | `schedule` | Intervalo (s), Estado (Activo/Inactivo) |
| Cámara | `photo_camera` | Conectada, Detectadas, Guardada en Pi, Dispositivo |
| Última config | `history` | Guardada (fecha locale es-ES) |
| Capturas | `collections` | Ciclos OK, Imágenes en disco |

**Reglas de presentación alimentación:**

| `power.power_source` | Fuente | Nivel |
|----------------------|--------|-------|
| `mains` | Corriente | 100 % |
| `usb` | USB / alimentación externa | 100 % |
| `powerbank` | Powerbank / batería | `display_percent` % |
| otro | `source_label` o — | `display_percent` o — |

**WiFi desconectado:** Activo = «No», resto «—».

---

### 5.3 Pestaña **WiFi** (`CardmedWifiWifiTab`)

**Layout:**

```
[Escanear redes]  (meta: modo escaneo si aplica)

SSID [select ▼]   Contraseña [____][👁]   [Conectar]
```

| Control | Estilo | Acción |
|---------|--------|--------|
| Escanear redes | Botón **primario** destacado (`wifi_find`) | `wifi_scan` |
| SSID | `<select>` compacto | Lista de redes |
| Contraseña | Input + ojo show/hide | — |
| Conectar | Botón primario | `wifi_connect` |

**API escaneo:**

```javascript
POST /api/command
Authorization: Bearer …
{ "cmd": "wifi_scan", "payload": { "rescan": true } }
// timeout 60 s
```

**Reglas UX escaneo:**

1. Espera mínima **4 s** en UI antes de mostrar «sin redes»
2. Mostrar hint mientras escanea: «El rescan en el Pi puede tardar unos segundos…»
3. Si `scan_mode` contiene `iw_fallback`: mostrar «Escaneo ampliado (rescan + iw)…»
4. **No cachear** redes entre sesiones
5. Opciones: `{ssid} ({signal} dBm)`

**API conectar:**

```javascript
{ "cmd": "wifi_connect", "payload": { "ssid": "…", "password": "…", "persist": true } }
// timeout 90 s
```

Tras conectar: refrescar dashboard + mostrar JSON de respuesta debajo (opcional, `<pre>` monospace).

**Grid CSS:** 3 columnas en desktop (`SSID | password | Conectar`), 1 columna en móvil.

---

### 5.4 Pestaña **Cámara** (`CardmedWifiCameraTab`)

**Fila superior (una línea en desktop):**

```
[Listar cámaras] [Guardar selección] [select cámara ▼] [Foto de prueba]
     primario      secundario borde      compacto           primario
```

| Botón | API |
|-------|-----|
| Listar cámaras | `camera_list` + `camera_get_device` |
| Guardar selección | `camera_set_device` `{ device: "/dev/video0" }` |
| Foto de prueba | `camera_capture_test` `{ device, mode: "base64" }` |

**Select:** solo `cam.name` (no path completo en dropdown). Max-width ~200px.

**Zona inferior (grid 2 columnas):**

```
┌─ Imagen prueba ─────────────┐  ┌─ Metadatos ──────────┐
│  [foto JPEG o placeholder]  │  │ Cámara, Dispositivo   │
│                             │  │ Driver, Bus           │
│                             │  │ Guardada en Pi        │
│                             │  │ Resolución            │
│                             │  │ Tamaño de la foto     │
│                             │  │ Backend, Modo captura │
└─────────────────────────────┘  └───────────────────────┘
```

**Imagen:** `data:image/jpeg;base64,{image_base64}`

**Metadatos captura:**

- Resolución: de API (`width`/`height`) o `img.naturalWidth/Height` en `onLoad`
- Tamaño: `size_bytes` de API, o estimar `(base64.length * 3) / 4`
- Backend, mode, sha256 si vienen en respuesta

**Placeholder sin foto:** icono `image` + «La imagen de prueba aparecerá aquí».

---

### 5.5 Pestaña **CardMed** (`CardmedWifiCardmedTab`)

**Toolbar:**

| Botón | API |
|-------|-----|
| Leer config | `cardmed_get` |
| Escanear QR (Pi) | `cardmed_scan_qr` `{ apply: true }` |
| Probar (primario) | `cardmed_test` `{ skip_upload: true }` |

**Bloques:**

1. **Código manual** — input `SITE|Sala|op|loc` + botón Guardar → `cardmed_configure` `{ config_code }`
2. **Config JSON** — textarea 5 filas + Guardar JSON → `cardmed_configure` `{ config_json }`
3. **Preview** — `<pre>` JSON de config cargada
4. **Resultado prueba** — lista ordenada de `steps[]` (rojo si `ok: false`) + JSON completo

---

### 5.6 Pantalla «Configuración completada»

```
✓ Configuración completada
  Vuelve a la WiFi con internet (oficina / MiniPC) para seguir usando NILO.

  [Seguir configurando]   [Cerrar ventana]
```

---

## 6. Cliente HTTP — especificación completa

### 6.1 Timeouts (ms)

| Operación | Timeout |
|-----------|---------|
| `/api/status` | 10 000 |
| `/api/dashboard` | 10 000 |
| `auth` | 30 000 |
| Comando genérico | 30 000 |
| `wifi_scan` | 60 000 |
| `wifi_connect`, `cardmed_test` | 90 000 |
| Espera mínima UI tras scan | 4 000 |

Implementar con `AbortController` + `cache: 'no-store'`.

### 6.2 Formato POST `/api/command`

```json
{
  "cmd": "nombre_comando",
  "id": "timestamp-string",
  "payload": { …campos… }
}
```

Respuesta:

```json
{ "ok": true, "cmd": "…", "data": { … } }
// error: { "ok": false, "error": "…" }
```

### 6.3 Cola de comandos

Serializar acciones del usuario (no lanzar dos comandos bloqueantes a la vez).  
Comandos bloqueantes conocidos: `wifi_scan`, `wifi_connect`, `camera_capture_test`, `cardmed_test`, etc.

### 6.4 Manejo de errores (textos exactos recomendados)

| Caso | Mensaje |
|------|---------|
| `Failed to fetch` / network | «No se pudo contactar con el Pi (192.168.4.1). Conecta la tablet al AP Nilocardmed-Config-xxxx.» |
| Timeout (`AbortError`) | «Tiempo de espera agotado. ¿Estás conectado a la WiFi Nilocardmed-Config-xxxx?» |
| HTTP 500 + `dashboard_failed` | «Error interno del Pi al generar el panel (dashboard_failed). Ejecuta update.sh…» |
| Auth fallida | «Contraseña incorrecta» |
| Sin token | «Sin sesión. Autentica primero con la contraseña del dispositivo.» |

### 6.5 Toasts / feedback

La PWA usa toasts (`toast.success`, `toast.error`, `toast.info`). En el Pi puedes usar:

- banners temporales,
- o la misma librería si portas el build.

Mensajes usados:

- «Sesión iniciada.»
- «Escaneando WiFi (rescan en el Pi, ~3–5 s mínimo)…»
- «N red(es) encontrada(s).»
- «WiFi configurado.»
- «Cámara guardada en el Pi.»
- «Foto de prueba capturada.»
- «Configuración guardada.» / «QR aplicado.» / «Prueba CardMed completada.»

---

## 7. Sistema de diseño (CSS)

La GUI usa **Material Design 3** con variables CSS del tema NILO:

```css
--m3-primary
--m3-on-primary
--m3-surface
--m3-surface-container-lowest
--m3-surface-container-high
--m3-surface-container-low
--m3-on-surface
--m3-on-surface-variant
--m3-outline-variant
--m3-primary-fixed
```

### Clases reutilizables

| Clase | Uso |
|-------|-----|
| `.nilo-cardmed__primary` | Botón principal: fondo primary, 44px alto, border-radius 12px, font-weight 700 |
| `.nilo-cardmed__secondary-scan` | Botón secundario: borde + fondo surface |
| `.nilo-cardmed__tab` / `--active` | Pestañas pill (36px, border-radius 9999px) |
| `.nilo-cardmed__panel` | Contenedor de pestaña (padding 20px, border-radius 16px) |
| `.nilo-cardmed__alert--error` | Banner error rojo |
| `.nilo-cardmed__hint` | Texto auxiliar gris 13px |
| `.nilo-cardmed__json` | `<pre>` fondo oscuro (#0f172a), texto #e2e8f0 |
| `.nilo-cardmed__capture` | Imagen captura: max-width 480px, border-radius 12px |

### Iconos

Material Symbols Outlined (Google). Nombres usados:

`lock`, `visibility`, `visibility_off`, `arrow_back`, `refresh`, `wifi`, `wifi_find`, `wifi_off`, `wifi_tethering`, `network_check`, `monitor_heart`, `photo_camera`, `videocam`, `save`, `image`, `medical_information`, `battery_charging_full`, `schedule`, `history`, `collections`, `check_circle`, `sync`

---

## 8. Tipos de datos (TypeScript de referencia)

Copiar del repo frontend:

```
src/features/doctor/cardmed/wifi/types.ts
src/features/doctor/cardmed/ble/types.ts  → WifiNetwork, CameraDevice, CardmedResponse
```

**Dashboard** (`CardmedDashboard`): `device_name`, `version`, `wifi`, `power`, `sampling`, `camera`, `captures`, `cardmed`, `config_last_saved_at`, `refreshed_at`.

**WifiNetwork:** `{ ssid, signal, security?, bssid?, frequency_mhz? }`

**CameraDevice:** `{ id, path, name, driver?, bus_info?, supports_capture? }`

**Captura base64:** `{ image_base64, size_bytes?, width?, height?, backend?, device_path?, mode? }`

---

## 9. Lógica auxiliar (copiar comportamiento)

Archivo `dashboard-utils.ts` — funciones clave:

- `buildDashboardStatusView(dashboard)` → strings para UI
- `formatDashboardPower(power)` → reglas mains/usb/powerbank
- `formatIsoDate(iso)` → `toLocaleString('es-ES')`
- Constante `DASHBOARD_NA = '—'`

Archivo `wifi-errors.ts`:

- `withWifiScanMinWait(promise)` → Promise.all con delay 4000ms
- `formatWifiScanMode(scan_mode)`
- `fetchDashboardWithFallback()` → GET dashboard, fallback POST device_status

---

## 10. Archivos fuente en NILO-frontend (referencia canónica)

Implementación React actual — **replicar comportamiento y layout**:

```
src/features/doctor/cardmed/
├── pages/
│   ├── CardmedWifiConfigView.tsx      ← VISTA PRINCIPAL (implementar en Pi)
│   ├── CardmedWifiPairView.tsx        ← NO portar (queda en tablet)
│   ├── CardmedWifiProvisionPage.tsx   ← Orquestador (solo referencia)
│   ├── CardmedWifiProvisionPage.css
│   └── CardmedDevicePage.css          ← tabs, primary, header, json
├── components/wifi/
│   ├── CardmedWifiLoginForm.tsx + .css
│   ├── CardmedWifiStatusTab.tsx + .css
│   ├── CardmedWifiWifiTab.tsx + .css
│   ├── CardmedWifiCameraTab.tsx + .css
│   ├── CardmedWifiCardmedTab.tsx + .css
│   └── CardmedWifiShared.css
├── hooks/
│   └── useCardmedWifiConnection.ts    ← estados phase/auth/command
├── wifi/
│   ├── NiloCardmedWifiClient.ts       ← fetch + auth + dashboard fallback
│   ├── constants.ts                     ← timeouts
│   ├── types.ts
│   └── wifi-errors.ts
```

**Atajo recomendado (opción A):** extraer estos componentes a un mini-proyecto Vite, build estático, copiar `dist/` al contenedor/servicio del Pi en `/var/www/cardmed/` o equivalente.

---

## 11. Cambio mínimo en la PWA tablet (otro agente Frontend)

En `CardmedWifiPairView` / `CardmedWifiProvisionPage`:

1. Mantener «Comprobar conexión» → `GET /api/status`
2. Si OK → `window.open('http://192.168.4.1:8080/', '_blank')`
3. **Eliminar** navegación interna a `CardmedWifiConfigView` (o dejarla detrás de flag apagado)
4. Opcional: tras abrir pestaña, mostrar banner «Se abrió la configuración en otra pestaña»

---

## 12. Checklist de paridad QA

- [ ] `GET /` sirve la GUI sin depender de internet externo
- [ ] Login → token → pestañas
- [ ] Estado: tarjetas con «—» sin datos; error rojo sin ocultar tarjetas; Refrescar 10s timeout
- [ ] WiFi: botón escaneo visible; scan `rescan:true`; espera 4s; ojo en contraseña
- [ ] Cámara: fila única de controles; preview + metadatos lado a lado
- [ ] CardMed: código, JSON, QR, Probar con steps
- [ ] Finalizar → pantalla done
- [ ] Tablet: Comprobar conexión abre pestaña al Pi
- [ ] Probar en tablet Android real en AP `Nilocardmed-Config-xxxx`

---

## 13. Documentación API adicional

| Doc | Contenido |
|-----|-----------|
| `docs/Integracion_Frontend_WiFi.md` | API HTTP detallada |
| `docs/BLUETOOTH_PROTOCOL.md` | Comandos JSON (válidos por HTTP) |
| `docs/MENSAJE_AGENTE_FRONTEND_WIFI_UI.md` | Briefing UI original |
| `docs/MENSAJE_AGENTE_FRONTEND_FIX_DASHBOARD_WIFI.md` | Fixes dashboard + scan |

---

## 14. Notas finales

1. **No hace falta** leer el SSID de la tablet desde JavaScript — el flujo asume que el usuario ya está en el AP antes de pulsar Comprobar conexión.
2. La GUI del Pi **no necesita** enlace «Volver al panel NILO» (no hay router externo); «Cerrar ventana» basta.
3. Mantener **español** en todos los textos de UI.
4. Priorizar **tablet landscape/portrait** (~720–900px ancho); layouts con `flex-wrap` y grids responsive como en los CSS de referencia.
