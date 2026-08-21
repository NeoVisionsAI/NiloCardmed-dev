#!/usr/bin/env bash
# Pip BLE: dbus-python + PyGObject (sin pycairo) + bluezero.
#
# Paquetes apt (instalar en Dockerfile antes de este script):
#   Compilación: gcc python3-dev pkg-config meson ninja-build
#   GObject/GLib: libglib2.0-dev libgirepository1.0-dev gobject-introspection
#   D-Bus:       libdbus-1-dev
#   Runtime (capa base): libgirepository-1.0-1 gir1.2-glib-2.0 libglib2.0-0 libdbus-1-3
#
# Pip (orden obligatorio):
#   meson-python → dbus-python → PyGObject (--no-build-isolation --no-deps -Dpycairo=disabled)
#   → bluezero (--no-deps)
#
# Validación local/CI: docker build -f docker/Dockerfile.ble-deps-test .
set -euo pipefail

pip install meson-python
pip install "dbus-python>=1.2,<3"

# bluezero/async_tools importa gi.repository.GLib — no usa cairo.
# --no-build-isolation evita pull de pycairo (libcairo2-dev) vía build isolation de pip.
pip install --no-build-isolation --no-deps "PyGObject>=3.42,<3.50" \
    --config-settings=setup-args="-Dpycairo=disabled"

pip install --no-deps "bluezero>=0.7,<1"

python -c "from gi.repository import GLib; import dbus; from bluezero import adapter; print('BLE deps OK')"
