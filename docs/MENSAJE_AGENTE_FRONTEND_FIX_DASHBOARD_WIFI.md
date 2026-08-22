# Fix urgente — Dashboard + WiFi scan (Frontend)

**Para:** agente Frontend  
**De:** Backend NiloCardmed  
**Fecha:** 2026-08-22

---

## 1. Dashboard «no se puede conectar» — CAUSA Y FIX

### Causa (backend, ya corregido)
`GET /api/dashboard` **petaba con error 500** por un bug (`window.reason` inexistente). El frontend lo interpretaba como fallo de conexión.

### Qué hacer en frontend

1. **URL correcta** — siempre la del Pi en el AP:
   ```javascript
   const DEVICE_API = 'http://192.168.4.1:8080';
   await fetch(`${DEVICE_API}/api/dashboard`, { cache: 'no-store' });
   ```
   **No** usar la URL HTTPS de la plataforma web.

2. **Precondición** — la tablet debe estar conectada a `Nilocardmed-Config-xxxx`. Fuera del AP, cualquier fetch a `192.168.4.1` fallará.

3. **Timeout** — mínimo **10 s** en refrescar dashboard.

4. **Manejo de errores** — distinguir:
   - `TypeError: Failed to fetch` → tablet no está en el AP / red incorrecta
   - HTTP 500 + `{ "error": "dashboard_failed" }` → bug backend (reportar); tras `update.sh` debería desaparecer
   - HTTP 200 → OK, pintar datos

5. **Alternativa autenticada** (si preferís POST):
   ```javascript
   await fetch(`${DEVICE_API}/api/command`, {
     method: 'POST',
     headers: {
       'Content-Type': 'application/json',
       Authorization: `Bearer ${token}`,
     },
     body: JSON.stringify({ cmd: 'device_status', payload: {} }),
   });
   ```

6. **Despliegue Pi** — el operador debe ejecutar:
   ```bash
   cd ~/dev/NiloCardmed-dev && sudo ./scripts/update.sh
   ```

---

## 2. WiFi scan — solo aparece la red conectada

### Causa
En Pi con **AP+STA concurrente** (`uap0` + `wlan0`), NetworkManager a veces solo lista la red activa en caché. **No es un cambio del frontend**; es limitación hardware + NM.

### Fix backend (ya aplicado)
- `wifi_scan` usa **`rescan: true` por defecto**
- Si NM devuelve ≤1 red → reintento con listado global + **`iw scan`** en el host

### Qué hacer en frontend

1. Llamar escaneo así:
   ```javascript
   await command('wifi_scan', { rescan: true });
   ```

2. **Esperar 3–5 s** tras pulsar «Escanear» antes de mostrar «sin redes» (el rescan en Pi tarda).

3. Mostrar `scan_mode` si viene en la respuesta (`rescan+iw_fallback` = usó escaneo alternativo).

4. No cachear resultados de scan entre sesiones.

---

## 3. Checklist rápido QA

- [ ] Tablet en AP `Nilocardmed-Config-xxxx`
- [ ] `GET http://192.168.4.1:8080/api/status` → `{ "status": "ok" }`
- [ ] `GET http://192.168.4.1:8080/api/dashboard` → JSON con `wifi`, `power`, `sampling`
- [ ] Botón Refrescar usa `DEVICE_API` (192.168.4.1), no dominio de producción
- [ ] WiFi scan espera ≥3 s y muestra todas las redes visibles

---

## 4. Referencia

- UI completa: `docs/MENSAJE_AGENTE_FRONTEND_WIFI_UI.md`
- API: `docs/Integracion_Frontend_WiFi.md`
