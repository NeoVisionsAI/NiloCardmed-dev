# Recuperación — Pi no arranca (pantalla negra / sin SSH)

Si tras `install.sh` / `update.sh` la Raspberry no muestra nada y no responde por SSH, **no tires la SD todavía**. Lo más habitual es un fichero de arranque o swap mal configurado, no hardware roto.

## Qué tocan nuestros scripts (y qué puede fallar)

| Script | Cambio | Riesgo de “no arranca” |
|--------|--------|-------------------------|
| `ensure-host-swap.sh` | `/var/swap` + línea en `/etc/fstab` | **Alto** si el archivo swap quedó a medias o corrupto → el boot puede quedarse colgado activando swap |
| `ensure-host-memory-optimize.sh` | `gpu_mem`, `DISABLE_GUI` | Medio: pantalla negra sin escritorio (SSH debería funcionar). Muy bajo `gpu_mem` puede dar imagen negra |
| `ensure-host-always-on.sh` | `cmdline.txt`, `config.txt`, lightdm | Bajo: `cmdline.txt` mal formado (raro). Lightdm roto = sin GUI, no sin SSH |
| `ensure-bluetooth-powered.sh` | BlueZ | Muy bajo |

**Importante:** ninguno de estos debería impedir SSH si el sistema llega a `multi-user.target`. Si tampoco hay SSH, suele ser **swap/fstab**, **SD llena/corrupta**, o **cmdline/config del firmware**.

---

## Paso 1 — Antes de sacar la SD (2 minutos)

1. Alimentación **oficial o buena USB** (Pi Zero 2 W es sensible).
2. Espera **3–5 minutos** tras encender (fsck en SD lenta).
3. Prueba SSH por IP y por hostname:
   ```bash
   ping cardmed.local
   ssh cardmed@<IP>
   ```
4. HDMI: puede estar en **consola** (texto negro con cursor) sin escritorio — no confundir con “apagada”.

---

## Paso 2 — Recuperación con la SD en otro PC

Apaga la Pi, saca la microSD e insértala en un PC (Linux/Mac; en Windows usa ext4 reader o WSL).

Monta las particiones (Bookworm suele ser):

- **Partición boot:** `bootfs` → `/boot/firmware/` (config.txt, cmdline.txt)
- **Partición root:** `rootfs` → `/etc/fstab`, etc.

### 2.1 Swap (causa más probable)

En la partición **root**, edita `etc/fstab`:

**Deja solo las 3 líneas estándar + UNA línea de swap** (o ninguna swap al principio):

```text
proc            /proc           proc    defaults          0       0
PARTUUID=7c87d4cb-01  /boot/firmware  vfat    defaults          0       2
PARTUUID=7c87d4cb-02  /               ext4    defaults,noatime  0       1
/var/swap none swap sw,nofail 0 0
```

**Borra las 6 líneas duplicadas** de `/var/swap`. Ese duplicado (bug del script) puede bloquear el arranque.

Si **sigue colgada tras arreglar fstab**, haz esto (en orden):

1. **Quita también la única línea de swap** — deja solo las 3 líneas `proc` / `PARTUUID` (sin `/var/swap`).
2. En la partición root, **borra el archivo** `var/swap` por completo (si existe).
3. Revisa `cmdline.txt` y `config.txt` (pasos 2.2 y 2.3).
4. Revisa `default.target` (paso 2.4).

Opcional: borra `var/swap` si existe y parece incompleto (< 1 GB).

### 2.2 Firmware (partición boot)

En `config.txt` (ruta `config.txt` en la partición boot):

```ini
gpu_mem=128
hdmi_blanking=0
```

Quita líneas duplicadas raras si las hay.

En `cmdline.txt` — **debe ser UNA sola línea**, sin saltos de línea extra. Debe contener `root=` y puede llevar `consoleblank=0` **una sola vez**. Ejemplo válido:

```text
console=serial0,115200 console=tty1 root=PARTUUID=xxxx rootfstype=ext4 fsck.repair=yes rootwait consoleblank=0
```

**Errores frecuentes que cuelgan el arranque:**

- Varias líneas en `cmdline.txt` (debe ser **una sola**).
- `consoleblank=0` repetido muchas veces al final.
- Falta `root=` o `rootwait`.

Si dudas: copia `cmdline.txt` desde una SD con Pi OS limpia del mismo modelo y solo cambia el `PARTUUID` por el tuyo (`7c87d4cb-02` en fstab → usa el de tu partición root).

### 2.3 Lightdm y arranque gráfico

En root, renombra (no borres):

```text
etc/lightdm/lightdm.conf.d/nilocardmed-no-blanking.conf
→ nilocardmed-no-blanking.conf.bak
```

Comprueba el target por defecto:

```text
etc/systemd/system/default.target
```

- Si apunta a `multi-user.target` y quieres escritorio: bórralo o enlázalo otra vez a `graphical.target` (como en Pi OS stock).
- Si `etc/systemd/system/lightdm.service` existe como enlace a `/dev/null` (masked), elimínalo para des-enmascarar lightdm.

### 2.4 Comprobar el sistema de ficheros (desde el PC)

Con la partición root montada en Linux:

```bash
sudo fsck -n /dev/sdX2    # solo lectura; sustituye sdX2 por tu partición ext4
```

Si reporta errores graves, `sudo fsck -y /dev/sdX2` (con la partición **desmontada**).

### 2.5 Volver a arrancar

1. Expulsa la SD con seguridad.
2. Vuelve a insertarla en la Pi y enciende.
3. Deberías recuperar SSH o al menos consola en HDMI.

---

## Paso 3 — Tras recuperar la Pi

Desde tu clone en `dev/` (con el repo actualizado):

```bash
cd ~/dev/NiloCardmed-dev
git pull
sudo ./scripts/update.sh --build
sudo reboot
```

Comprueba espacio antes de recrear swap:

```bash
df -h /var
free -h
```

---

## Si sigue sin arrancar

- Prueba otra fuente de alimentación / cable USB.
- Otra SD con imagen limpia de Raspberry Pi OS y reinstalar NiloCardmed.
- Los datos en `/var/lib/nilocardmed/data` pueden copiarse desde la SD antigua si el sistema de ficheros root no está muy corrupto.
