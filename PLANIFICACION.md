# NiloCardmed-dev — Descripción y planificación

## 1. Descripción del proyecto

**NiloCardmed-dev** es un software embebido diseñado para ejecutarse en una **Raspberry Pi Zero W2**. Su función principal es capturar imágenes periódicamente desde una cámara USB y enviarlas a un servidor remoto (**SER**), donde existe desplegada una API REST que recibe esas imágenes.

Además del flujo automático de captura y envío, el dispositivo expone un **servicio Bluetooth** que permite a operadores cercanos conectarse (mediante contraseña) para configurar, probar y monitorizar el sistema sin necesidad de acceso físico directo a la Raspberry.

### 1.1 Plataforma y despliegue

| Aspecto | Detalle |
|---------|---------|
| Hardware | Raspberry Pi Zero W2 |
| Periféricos | Cámara USB conectada al puerto USB |
| Empaquetado | Imagen Docker |
| Política de arranque | El contenedor debe iniciarse automáticamente al reiniciar la Raspberry y reiniciarse si falla |
| Conectividad principal | WiFi hacia la red desplegada en SER |
| Conectividad local | Bluetooth habilitado para configuración remota |

### 1.2 Flujo principal (operación automática)

1. El software se conecta a la red WiFi configurada (red de SER o la definida por el operador).
2. Cada **X segundos** (intervalo configurable), captura una fotografía desde la cámara USB.
3. Envía la imagen capturada a la **API REST** desplegada en SER.
4. Repite el ciclo dentro de la ventana de monitorización configurada (inicio/fin), o de forma continua si ambos valores son `-1`.

### 1.3 Servicio Bluetooth (configuración y diagnóstico)

El Bluetooth permanece activo para que otros dispositivos puedan **descubrir y conectarse** a la Raspberry Pi Zero. La conexión requiere **autenticación por contraseña**.

Una vez autenticado, el cliente Bluetooth puede ejecutar las siguientes operaciones:

| # | Operación | Descripción |
|---|-----------|-------------|
| 1 | Listar cámaras | Devuelve las cámaras USB detectadas en el dispositivo |
| 2 | Imagen de prueba | Captura y devuelve una imagen de prueba de la cámara seleccionada |
| 3 | Configurar CardMed | Aplica la configuración principal del sistema CardMed |
| 4 | Probar CardMed | Ejecuta una prueba del flujo CardMed (captura + envío o validación local) |
| 5 | Intervalo de muestreo | Define cada cuántos segundos se capturan y envían muestras |
| 6 | Ventana de monitorización | Define instante de inicio y fin del muestreo (`-1` / `-1` = sin parar) |
| 7 | Configuración WiFi | Escanea redes, permite introducir SSID y contraseña, comprueba conexión |
| — | *Extensible* | La arquitectura debe permitir añadir nuevas operaciones en el futuro |

### 1.4 Actores y contexto

```
┌─────────────────────┐         WiFi          ┌─────────────────────┐
│  Raspberry Pi Zero  │ ────────────────────► │        SER          │
│  W2 + Cámara USB    │    (capturas HTTP)    │   API REST          │
│  [NiloCardmed-dev]  │                       │                     │
└─────────┬───────────┘                       └─────────────────────┘
          │
          │ Bluetooth (configuración)
          ▼
┌─────────────────────┐
│  Dispositivo móvil  │
│  / tablet operador  │
└─────────────────────┘
```

### 1.5 Restricciones técnicas relevantes

- **Pi Zero W2**: recursos limitados (512 MB RAM, CPU ARM). El diseño debe ser ligero.
- **Docker en ARM**: la imagen debe construirse para `linux/arm/v6` o `arm/v7` según la base elegida.
- **Bluetooth y USB desde Docker**: requiere acceso al host (`/dev/bus/usb`, D-Bus, posiblemente modo `privileged` o capacidades específicas).
- **WiFi desde contenedor**: la gestión de redes suele hacerse en el host; conviene definir si la configuración WiFi modifica el sistema host o delega en un servicio auxiliar.
- **Persistencia**: la configuración (WiFi, intervalo, ventana, credenciales SER, contraseña Bluetooth) debe sobrevivir reinicios.

---

## 2. Arquitectura propuesta (visión de alto nivel)

```
nilocardmed/
├── config/              # Gestión de configuración persistente
├── camera/              # Detección y captura USB (V4L2 / OpenCV / fswebcam)
├── sampler/             # Motor de muestreo periódico y ventana temporal
├── ser_client/          # Cliente HTTP hacia API REST de SER
├── bluetooth/           # Servidor GATT/BLE + protocolo de comandos
├── wifi/                # Escaneo, configuración y verificación de conexión
├── cardmed/             # Lógica específica CardMed (config + prueba)
└── main.py              # Orquestador principal
```

**Stack tecnológico recomendado:**

- **Lenguaje**: Python 3.11+ (coherente con el `.gitignore` existente).
- **Contenedor**: Docker multi-stage, base `python:3.11-slim-bookworm` para ARM.
- **Arranque automático**: `restart: unless-stopped` en Docker Compose + servicio `systemd` que levante Compose al boot.
- **Bluetooth**: BlueZ + `dbus-next` o biblioteca BLE (p. ej. `bleak` en cliente; en servidor, GATT expuesto vía BlueZ/dbus o `bluezero`).
- **Cámara**: `v4l2` / `opencv-python-headless` / `fswebcam`.
- **HTTP**: `httpx` o `requests` hacia SER.
- **Configuración**: fichero JSON/YAML en volumen persistente (`/data/config.json`).

---

## 3. Fases de desarrollo

Las fases están ordenadas por dependencias. Cada fase debe quedar **funcional y verificable** antes de pasar a la siguiente.

---

### Fase 0 — Estructura base del proyecto

**Objetivo:** Sentar las bases del repositorio y el esqueleto ejecutable.

**Entregables:**
- Estructura de directorios y módulos Python.
- `pyproject.toml` / `requirements.txt` con dependencias mínimas.
- Sistema de configuración con valores por defecto y fichero persistente.
- Punto de entrada (`main.py`) con logging estructurado.
- Variables de entorno documentadas (URL SER, intervalo, etc.).

**Criterio de aceptación:** El contenedor (aún sin hardware) arranca, lee/escribe config y registra logs.

---

### Fase 1 — Dockerización y arranque automático

**Objetivo:** Empaquetar la aplicación y garantizar despliegue resiliente en la Pi.

**Entregables:**
- `Dockerfile` optimizado para ARM (Pi Zero W2).
- `docker-compose.yml` con:
  - `restart: unless-stopped`
  - volúmenes para config y logs
  - mapeo de dispositivos USB (`/dev/video0`, etc.)
- Unidad `systemd` (`nilocardmed.service`) que ejecute `docker compose up -d` al boot.
- Script de instalación inicial en la Pi (`scripts/install.sh`).

**Criterio de aceptación:** Tras reiniciar la Raspberry, el contenedor vuelve a estar en ejecución. Si se mata el proceso, Docker lo reinicia.

---

### Fase 2 — Captura de cámara USB

**Objetivo:** Detectar cámaras y capturar imágenes bajo demanda.

**Entregables:**
- Módulo `camera/` con:
  - listado de dispositivos de vídeo disponibles
  - captura a buffer/fichero temporal (JPEG)
  - manejo de errores (cámara desconectada, ocupada, etc.)
- Comando CLI o endpoint interno de prueba: `capture-test`.

**Criterio de aceptación:** Con una cámara USB conectada, el software lista la cámara y guarda una imagen válida.

---

### Fase 3 — Cliente REST hacia SER

**Objetivo:** Enviar imágenes capturadas a la API de SER.

**Entregables:**
- Módulo `ser_client/` con cliente HTTP configurable (URL base, token/API key si aplica).
- Formato de envío acordado con SER (multipart, base64, etc. — a definir con el equipo de SER).
- Reintentos con backoff ante fallos de red.
- Registro de éxito/error por muestra.

**Criterio de aceptación:** Una captura de prueba se envía correctamente a SER y la API responde 2xx.

---

### Fase 4 — Motor de muestreo periódico

**Objetivo:** Automatizar captura + envío según intervalo y ventana temporal.

**Entregables:**
- Módulo `sampler/` con:
  - intervalo configurable en segundos
  - ventana de monitorización (inicio/fin; `-1`/`-1` = infinito)
  - bucle principal asyncio o thread dedicado
  - parada/arranque limpio ante cambios de configuración
- Integración con config persistente.

**Criterio de aceptación:** El sistema captura y envía cada X segundos; respeta inicio/fin; con `-1/-1` no se detiene.

---

### Fase 5 — Gestión WiFi

**Objetivo:** Permitir escanear, configurar y verificar la conexión WiFi del dispositivo.

**Entregables:**
- Módulo `wifi/` con:
  - escaneo de redes disponibles (SSID, señal)
  - aplicación de credenciales (SSID + contraseña)
  - comprobación de estado de conexión e IP
- Decisión de implementación documentada:
  - **Opción A (recomendada):** servicio en el host (NetworkManager/`wpa_supplicant`) invocado desde el contenedor vía script montado.
  - **Opción B:** contenedor con `--network host` y herramientas `nmcli`/`iw`.
- Persistencia de la red configurada.

**Criterio de aceptación:** Desde una prueba manual (CLI), se listan redes, se configura una WiFi y se confirma conectividad.

---

### Fase 6 — Servicio Bluetooth con autenticación

**Objetivo:** Exponer el dispositivo vía BLE/Bluetooth con acceso protegido por contraseña.

**Entregables:**
- Módulo `bluetooth/` con:
  - advertising/discoverability del dispositivo
  - servicio GATT con características de comando/respuesta
  - handshake de autenticación (contraseña configurable, almacenada de forma segura)
  - protocolo de mensajes extensible (JSON sobre GATT o similar)
- Documentación del protocolo para desarrolladores de apps cliente.

**Criterio de aceptación:** Un dispositivo externo descubre la Pi, se autentica con contraseña y recibe respuesta de un comando ping.

---

### Fase 7 — Operaciones Bluetooth (cámara, muestreo, WiFi)

**Objetivo:** Implementar todas las operaciones expuestas por Bluetooth.

**Entregables:**

| Comando BT | Integración |
|------------|-------------|
| Listar cámaras | Fase 2 |
| Imagen de prueba | Fase 2 |
| Definir intervalo | Fase 4 + config |
| Ventana monitorización | Fase 4 + config |
| Configurar WiFi | Fase 5 |
| Comprobar WiFi | Fase 5 |

- Respuestas estructuradas (JSON) con códigos de error claros.
- Punto de extensión para futuros comandos (registro de handlers).

**Criterio de aceptación:** Todas las operaciones 1–7 funcionan desde un cliente Bluetooth de prueba.

---

### Fase 8 — CardMed: configuración y prueba

**Objetivo:** Integrar la lógica de negocio específica de CardMed.

**Entregables:**
- Módulo `cardmed/` con:
  - esquema de configuración CardMed (campos a concretar con negocio)
  - operación *Configurar CardMed* vía Bluetooth
  - operación *Probar CardMed* (flujo completo: captura → procesamiento → envío/validación)
- Validaciones y feedback al operador.

**Criterio de aceptación:** Un operador configura CardMed por Bluetooth y ejecuta una prueba end-to-end satisfactoria.

---

### Fase 9 — Integración, pruebas en hardware y endurecimiento

**Objetivo:** Validar el sistema completo en Raspberry Pi Zero W2 real.

**Entregables:**
- Pruebas de estrés (muestreo prolongado, reconexión WiFi, reinicios).
- Optimización de memoria/CPU para Pi Zero W2.
- Manejo de escenarios de fallo: sin WiFi, sin cámara, SER caído, batería baja (si aplica).
- README de despliegue y guía de operación para el operador Bluetooth.
- (Opcional) tests automatizados con mocks para CI en x86.

**Criterio de aceptación:** El dispositivo opera 24 h sin intervención en condiciones nominales; se recupera solo tras reinicio o fallo.

---

## 4. Resumen de fases

| Fase | Nombre | Dependencias |
|------|--------|--------------|
| 0 | Estructura base | — |
| 1 | Docker y arranque automático | 0 |
| 2 | Captura cámara USB | 0, 1 |
| 3 | Cliente REST → SER | 0, 2 |
| 4 | Motor de muestreo | 2, 3 |
| 5 | Gestión WiFi | 0, 1 |
| 6 | Bluetooth + autenticación | 0, 1 |
| 7 | Operaciones Bluetooth | 2, 4, 5, 6 |
| 8 | CardMed (config + prueba) | 3, 4, 7 |
| 9 | Integración y endurecimiento | Todas |

---

## 5. Decisiones pendientes (a concretar antes/durante la implementación)

1. **Contrato API con SER**: URL, método HTTP, formato del body, autenticación, respuesta esperada.
2. **Parámetros CardMed**: qué campos exactos incluye *Configurar CardMed* y qué valida *Probar CardMed*.
3. **Bluetooth**: BLE (GATT) vs Bluetooth clásico RFCOMM — recomendable BLE para apps móviles modernas.
4. **WiFi**: ¿NetworkManager en Raspberry Pi OS? ¿Imagen base del host?
5. **Seguridad**: almacenamiento de contraseña Bluetooth, credenciales WiFi y tokens SER (cifrado en reposo, permisos de volumen).
6. **App cliente Bluetooth**: ¿app móvil existente, nRF Connect, o cliente de prueba propio en esta fase?

---

## 6. Próximo paso

Comenzar por la **Fase 0** (estructura base del proyecto) y, en paralelo si es posible, preparar la **Fase 1** (Dockerfile + compose) para poder probar en la Pi cuanto antes.

Cuando indiques que empezamos, abordamos la Fase 0 y te voy mostrando cada entregable antes de pasar a la siguiente.
