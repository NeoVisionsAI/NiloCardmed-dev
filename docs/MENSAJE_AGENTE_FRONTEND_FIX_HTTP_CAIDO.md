# Urgente — HTTP 192.168.4.1:8080 no responde

**Para:** agente Frontend  
**De:** Backend NiloCardmed

---

## Diagnóstico

Si **ni el navegador** (`http://192.168.4.1:8080`) **ni** «Comprobar conexión» funcionan, el problema **no es solo frontend**: el servidor HTTP del Pi no está accesible o la tablet no tiene IP en el AP.

### Causas backend (corregidas en este update)

1. Bug `/api/dashboard` (500) — **no afecta** a `/api/status` ni a `/`
2. HTTP podía **fallar al arrancar** (puerto 8080 ocupado) y el log decía «activo» igualmente → **corregido**
3. Firewall AP sin regla explícita TCP 8080 → **añadida**
4. `update.sh` ahora **reinicia el contenedor** si HTTP no responde

---

## Frontend — «Comprobar conexión»

Debe usar **`/api/status`**, no `/api/dashboard`:

```javascript
const DEVICE_API = 'http://192.168.4.1:8080';

async function checkDeviceReachable() {
  try {
    const res = await fetch(`${DEVICE_API}/api/status`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(10000),
    });
    if (!res.ok) return false;
    const data = await res.json();
    return data.status === 'ok';
  } catch {
    return false;
  }
}
```

**Refrescar panel Estado** (tras login): `GET ${DEVICE_API}/api/dashboard`

| Error | Significado |
|-------|-------------|
| `Failed to fetch` | Tablet no en AP o sin IP (DHCP) |
| Timeout 10 s | HTTP caído en Pi o firewall |
| HTTP 500 dashboard | Bug backend (tras `update.sh` debería desaparecer) |
| HTTP 200 | OK |

**Importante:** mientras configuras el dispositivo, la tablet debe estar en **`Nilocardmed-Config-xxxx`**, no en la WiFi de oficina.

---

## Operador — en la Pi (ahora)

```bash
cd ~/dev/NiloCardmed-dev
sudo ./scripts/update.sh
```

Si sigue fallando:

```bash
sudo systemctl restart nilocardmed
sudo /opt/nilocardmed/scripts/wifi-ap-run.sh repair-dhcp
sudo /opt/nilocardmed/scripts/wifi-ap-run.sh status
curl -s http://127.0.0.1:8080/api/status
curl -s http://192.168.4.1:8080/api/status
```

En `status` debe salir:
- `dhcp: escuchando UDP 67`
- `http: OK → http://192.168.4.1:8080/api/status`

Si `http: NO RESPONDE`:
```bash
sudo docker logs nilocardmed 2>&1 | tail -40
sudo ss -tlnp | grep ':8080'
```

Pega esa salida si hay que seguir depurando.

---

## Checklist frontend

- [ ] `checkDeviceReachable` → `GET /api/status` (no dashboard)
- [ ] `DEVICE_API = 'http://192.168.4.1:8080'` (literal, sin HTTPS)
- [ ] Timeout ≥ 10 s
- [ ] Refrescar dashboard solo **después** de login y solo si `checkDeviceReachable()` fue OK
- [ ] Mensaje claro si `Failed to fetch`: «Conecta la tablet a Nilocardmed-Config-xxxx y espera a obtener IP»
