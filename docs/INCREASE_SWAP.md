# Problema de Rendimiento y Congelamiento Gráfico en Raspberry Pi Zero 2 W

## 1. Descripción del Problema
En la Raspberry Pi Zero 2 W (que cuenta con **512 MB de RAM física**), al ejecutar un entorno gráfico o servicios en segundo plano, la memoria RAM se satura rápidamente. 

Cuando esto ocurre:
1. El sistema operativo recurre intensivamente a la memoria **Swap** para mover páginas de memoria inactivas.
2. Por defecto, Raspberry Pi OS configura una Swap muy reducida (aprox. 100 MB a 400 MB).
3. Al agotarse la Swap o trabajar al límite, el subsistema gráfico (**VideoCore / Wayland / X11**) pierde capacidad de respuesta:
   - La pantalla HDMI entra en suspensión o apaga la señal.
   - Al intentar "despertar" la interfaz o mover el cursor, el sistema sufre micro-congelaciones drásticas (*lag* severo) debido al cuello de botella de lectura/escritura en la tarjeta MicroSD.
4. No es un problema de sobrecalentamiento (*throttling*) ni de caídas de voltaje si `vcgencmd measure_temp` y `vcgencmd get_throttled` arrojan valores normales.

---

## 2. Solución: Script de Auto-Configuración de Swap (1 GB)

Para que el agente de despliegue automatice la configuración la primera vez que se inicie el sistema, se debe incluir un script o tarea de aprovisionamiento que verifique el tamaño de la Swap actual y, si es menor a **1024 MB**, la reconfigure automáticamente a 1 GB.

> **Automatizado en NiloCardmed:** `sudo ./scripts/install.sh` (primera vez). **`update.sh` no toca swap** en caliente (evita colgar SSH en Pi Zero 2 W).

> **Nota:** No se recomienda asignar más de 1 GB de Swap en tarjetas MicroSD para evitar degradación prematura por ciclos de escritura y ralentizaciones severas de I/O.

Variables opcionales:

| Variable | Default | Descripción |
|----------|---------|-------------|
| `NILOCARDMED_SWAP_SIZE_MB` | `1024` | Tamaño objetivo al crear el fichero |
| `NILOCARDMED_SWAP_MIN_REPORTED_MB` | `1000` | Si `free -m` ≥ esto y existe `/var/swap`, no se recrea |
| `NILOCARDMED_SWAP_MIN_FILE_MB` | `1000` | Si el fichero ≥ esto (p. ej. 1023–1024), no se vuelve a hacer `dd` |
| `NILOCARDMED_SWAP_FILE` | `/var/swap` | Ruta del archivo swap |
| `DISABLE_GUI` | `false` | Sin escritorio; arranque consola (`deploy.env`) |
| `OPTIMIZE_GPU_MEM` | `true` | `gpu_mem=16` **solo si** `DISABLE_GUI=true` (con escritorio no se aplica) |
| `ENABLE_HOST_SWAP` | `true` | Si `false`, no toca `/etc/fstab` ni `/var/swap` |

> **Ratón lento / escritorio pesado:** si `gpu_mem=16` quedó aplicado con escritorio activo, sube la RAM de GPU y reinicia:
> ```bash
> sudo sed -i 's/^gpu_mem=.*/gpu_mem=128/' /boot/firmware/config.txt
> sudo reboot
> ```

### Pantalla siempre encendida (automático)

En cada `install.sh` / `update.sh` se ejecuta `scripts/ensure-host-always-on.sh`, equivalente a:

- **raspi-config** → *Display Options* → *Screen Blanking* → **No**
- `hdmi_blanking=0` y `consoleblank=0` en firmware/kernel
- systemd: sin suspender/hibernar por inactividad
- lightdm / X11: DPMS desactivado (`xset s off -dpms` en autostart LXDE; **no** se modifica `xserver-command` de lightdm — rompe el escritorio en Pi OS)

Reinicio recomendado tras el primer despliegue.

---

## Incidente: Pi que no arranca tras `update.sh` (causa raíz)

### Qué pasó

El script `ensure-host-swap.sh` comprobaba si `/var/swap` ya estaba en `/etc/fstab` con un `grep` **incorrecto**:

```bash
# BUG (exigía espacio ANTES de /var/swap; las líneas empiezan con /var/swap)
grep -qE "[[:space:]]/var/swap[[:space:]]"
```

Cada `sudo ./scripts/update.sh` **añadía otra línea** a fstab. Con 6 líneas, el arranque intentaba activar la misma swap varias veces y **se colgaba** (sin SSH, pantalla negra tras el arco iris).

Además, el script usaba `swapoff -a` al crear la swap. En una Pi Zero 2 W (512 MB) con Docker en marcha, eso puede provocar **OOM** y **corrupción del ext4**, de modo que aunque arreglaras fstab, la SD seguía inutilizable hasta `fsck` o re-flash.

### Qué NO fue la causa

| Cambio | ¿Rompe el arranque? |
|--------|---------------------|
| `cmdline.txt` / `consoleblank=0` | No (tu cmdline era válido) |
| `hdmi_blanking=0` | No |
| `gpu_mem=16` | Ratón lento, no brick |
| Partición boot “vacía” en `root/boot/firmware` | Normal (montaje separado) |
| Pantalla always-on / logind | No impide SSH |

### Correcciones en el repo

- Detección fstab con `^/var/swap` (inicio de línea).
- `dedupe_fstab_swap` en cada ejecución.
- Entrada fstab con `sw,nofail`.
- No usar `swapoff -a` (solo desactiva `/var/swap` concreto).
- `mkswap` + `swapon` OK antes de tocar fstab.
- `gpu_mem=16` solo si `DISABLE_GUI=true`.
- `ENABLE_HOST_SWAP=false` en `deploy.env` para omitir swap automática.

### Tras reinstalar Pi OS

```bash
cd ~/dev/NiloCardmed-dev
git pull
sudo ./scripts/install.sh
# Comprobar fstab ANTES del primer reboot:
grep swap /etc/fstab    # debe haber 0 o 1 línea /var/swap
sudo reboot
```

Si la SD es ≤ 32 GB o está muy llena: `ENABLE_HOST_SWAP=false` en `deploy.env` hasta ampliar almacenamiento.

Ver también: [RECUPERACION_ARRANQUE.md](RECUPERACION_ARRANQUE.md)

---

## 3. Pasos para la Integración Automatizada

### Opción A: Script de Shell Bash (`setup_swap.sh`)

Crea o integra el siguiente script en la rutina de despliegue/primer arranque:

```bash
#!/usr/bin/env bash
set -e

# Tamaño deseado en Megabytes
DESIRED_SWAP_MB=1024

# Obtener tamaño actual de Swap en MB
CURRENT_SWAP_MB=$(free -m | awk '/^Swap:/ {print $2}')

echo "[*] Evaluando memoria Swap actual: ${CURRENT_SWAP_MB} MB..."

if [ "$CURRENT_SWAP_MB" -lt "$DESIRED_SWAP_MB" ]; then
    echo "[!] La Swap actual (${CURRENT_SWAP_MB} MB) es inferior a los ${DESIRED_SWAP_MB} MB recomendados."
    echo "[*] Reconfigurando Swap a ${DESIRED_SWAP_MB} MB en /var/swap..."

    # Desactivar Swap activa
    sudo swapoff -a || true

    # Crear nuevo archivo de Swap de 1GB
    sudo dd if=/dev/zero of=/var/swap bs=1M count=${DESIRED_SWAP_MB} status=progress

    # Asignar permisos estrictos de seguridad
    sudo chmod 600 /var/swap

    # Formatear archivo como espacio Swap
    sudo mkswap /var/swap

    # Activar la nueva Swap
    sudo swapon /var/swap

    # Asegurar que persiste en /etc/fstab (si no existe la entrada)
    if ! grep -q "/var/swap" /etc/fstab; then
        echo "/var/swap none swap sw 0 0" | sudo tee -a /etc/fstab
    fi

    echo "[✓] Swap configurada exitosamente a ${DESIRED_SWAP_MB} MB."
else
    echo "[✓] La Swap actual (${CURRENT_SWAP_MB} MB) ya es suficiente. No se requieren cambios."
fi
```

---

### Opción B: Integración en Ansible / Playbook de Despliegue

Si el agente de despliegue utiliza **Ansible**, añade la siguiente tarea:

```yaml
- name: Obtener tamaño de la memoria Swap actual (MB)
  ansible.builtin.setup:
    filter: ansible_swaptotal_mb

- name: Reconfigurar Swap a 1024 MB si es inferior
  when: ansible_swaptotal_mb < 1024
  block:
    - name: Desactivar Swap actual
      ansible.builtin.command: swapoff -a
      failed_when: false

    - name: Crear archivo de Swap de 1 GB
      ansible.builtin.command: dd if=/dev/zero of=/var/swap bs=1M count=1024
      args:
        creates: /var/swap

    - name: Establecer permisos en el archivo de Swap
      ansible.builtin.file:
        path: /var/swap
        owner: root
        group: root
        mode: '0600'

    - name: Formatear archivo como Swap
      ansible.builtin.command: mkswap /var/swap

    - name: Activar Swap
      ansible.builtin.command: swapon /var/swap

    - name: Asegurar persistencia en /etc/fstab
      ansible.builtin.mount:
        path: none
        src: /var/swap
        fstype: swap
        opts: sw
        state: present
```

---

## 4. Verificación Post-Despliegue

Una vez ejecutado el despliegue, el agente puede verificar el estado ejecutando:

```bash
free -h
# La fila 'Swap:' debe mostrar un total de ~1.0Gi
```