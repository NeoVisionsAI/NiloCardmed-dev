# Guía del operador (tablet Android)

Instrucciones para configurar y probar un NiloCardmed en campo usando una **app web** y **Bluetooth**.

Requisitos: tablet Android, Chrome, app servida por HTTPS, dispositivo `NiloCardmed` encendido y al alcance BLE.

Documentación técnica: [WEB_BLUETOOTH_CLIENT.md](WEB_BLUETOOTH_CLIENT.md)

## 1. Encender y localizar el dispositivo

1. Conecta la Pi a alimentación; espera ~30 s al arranque.
2. En la app, pulsa **Conectar dispositivo**.
3. Selecciona **NiloCardmed** (o el nombre configurado).
4. Acepta permisos Bluetooth.

## 2. Autenticación

1. Introduce la **contraseña Bluetooth** (proporcionada por administración).
2. La app guarda el **token** para comandos posteriores.
3. Si aparece `unauthorized`, vuelve a autenticarte.

## 3. Comprobar estado

Ejecuta (o usa botones equivalentes en la app):

| Acción BLE | Comando |
|------------|---------|
| Estado general | `health_status` |
| WiFi | `wifi_status` |
| Config CardMed | `cardmed_get` |

Interpretación de `health_status`:

- `healthy: true` — operación nominal
- `wifi` FAIL — configurar red
- `camera` FAIL — revisar USB
- `sampler` FAIL — revisar logs / fallos consecutivos

## 4. Configurar WiFi

1. `wifi_scan` — listar redes
2. `wifi_connect` con `ssid`, `password`, `persist: true`
3. `wifi_test` — confirmar conectividad

Tras conectar, el dispositivo guarda credenciales en `config.json` y reconectará solo tras reinicios.

## 5. Configurar CardMed

```json
{
  "cmd": "cardmed_configure",
  "site_id": "SITE-001",
  "device_label": "Sala 3",
  "location": "Planta 1",
  "operator_id": "tu-id",
  "metadata": {"ward": "cardiology"}
}
```

Confirma con `cardmed_get`.

## 6. Probar CardMed

```json
{"cmd": "cardmed_test", "skip_upload": false}
```

Revisa `data.steps`:

| Paso | Significado |
|------|-------------|
| `cardmed_enabled` | CardMed activo |
| `wifi_connected` | Red OK |
| `connectivity` | Internet/SER alcanzable |
| `capture` | Foto OK |
| `validate_image` | JPEG válido |
| `upload` | Enviado a SER |

Si falla un paso, el mensaje indica la causa (`wifi_not_connected`, `no_camera`, etc.).

## 7. Ajustar muestreo

| Objetivo | Comando |
|----------|---------|
| Intervalo | `sampling_set_interval` → `interval_seconds` |
| Ventana horaria | `sampling_set_window` → `monitor_start`, `monitor_end` |
| Ver config | `sampling_get` |

Valores `-1` en ventana = muestreo continuo.

## 8. Prueba de cámara (opcional)

- `camera_list` — ver dispositivos
- `camera_capture_test` con `mode: "chunked"` — imagen por BLE
- Descargar chunks con `camera_capture_chunk`

## 9. Problemas frecuentes

| Síntoma | Solución |
|---------|----------|
| No aparece en escaneo BLE | Verificar `ENABLE_BLUETOOTH` en Pi; reiniciar servicio |
| JSON truncado / error parse | Reensamblar frames BLE `{t:"f",…}` (ver guía web) |
| `invalid_password` | Contraseña incorrecta en `.env` |
| Muestreo parado | `health_status` → sampler; comprobar WiFi y cámara |
| Sin envío a SER | `cardmed_test`; revisar URL SER y WiFi |

## 10. Contacto con soporte

Recopilar antes de escalar:

```bash
docker compose logs --tail=200 > logs.txt
docker compose exec nilocardmed python -m nilocardmed.main health status
```

Enviar `logs.txt` + resultado de `health status` (sin contraseñas).
