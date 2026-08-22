"""Estado del anuncio LE (BlueZ) y limpieza de registros obsoletos."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
GATT_APP_IFACE = "org.bluez.GattApplication1"
LE_ADVERT_IFACE = "org.bluez.LEAdvertisement1"


def read_le_advertising_state(*, timeout: float = 5.0) -> dict[str, Any]:
    """Lee Advertising / ActiveInstances desde ``bluetoothctl show``."""
    from nilocardmed.bluetooth.adapter_visibility import read_adapter_state

    state = read_adapter_state(timeout=timeout)
    active_raw = state.get("active_instances") or state.get("activeinstances") or "0x00 (0)"
    active_count = 0
    if "(" in active_raw:
        try:
            active_count = int(active_raw.split("(")[1].split(")")[0])
        except ValueError:
            active_count = 0

    advertising = state.get("advertising", "").lower()
    advertising_yes = advertising == "yes"

    return {
        "alias": state.get("alias"),
        "powered": state.get("powered") == "yes",
        "discoverable": state.get("discoverable") == "yes",
        "pairable": state.get("pairable") == "yes",
        "advertising": advertising_yes if advertising else None,
        "active_instances": active_count,
        "le_advertising_active": advertising_yes or active_count > 0,
        "raw": state,
    }


def is_le_advertisement_registered(advert_path: str) -> bool:
    """Indica si ``advert_path`` sigue publicado en BlueZ."""
    try:
        import dbus
    except ImportError:
        return False

    try:
        bus = dbus.SystemBus()
        om = dbus.Interface(
            bus.get_object(BLUEZ_SERVICE, "/"),
            DBUS_OM_IFACE,
        )
        objects = om.GetManagedObjects()
        for path, interfaces in objects.items():
            if str(path) == advert_path and LE_ADVERT_IFACE in interfaces:
                return True
        return False
    except Exception as exc:
        logger.debug("No se pudo comprobar LEAdvertisement %s: %s", advert_path, exc)
        return False


def unregister_le_advertisement_path(
    advert_path: str,
    *,
    adapter_address: str | None = None,
) -> bool:
    """Desregistra un anuncio LE concreto si el manager del adaptador está disponible."""
    try:
        import dbus
    except ImportError:
        return False

    from bluezero import dbus_tools

    try:
        bus = dbus.SystemBus()
        adapter_path = None
        if adapter_address:
            adapter_path = dbus_tools.get_dbus_path(adapter=adapter_address)
        if adapter_path is None:
            return False
        ad_manager = dbus.Interface(
            bus.get_object(BLUEZ_SERVICE, adapter_path),
            LE_ADVERTISING_MANAGER_IFACE,
        )
        ad_manager.UnregisterAdvertisement(advert_path)
        logger.info("LE advertisement desregistrado: %s", advert_path)
        return True
    except Exception as exc:
        logger.debug("UnregisterAdvertisement %s: %s", advert_path, exc)
        return False


def purge_stale_bluez_registrations(
    adapter_address: str | None = None,
    *,
    aggressive: bool = False,
) -> int:
    """Desregistra aplicaciones GATT y anuncios LE huérfanos en BlueZ.

    Con ``aggressive=True`` elimina cualquier GATT/advert del adaptador (no solo
    rutas bluezero), útil cuando ``ActiveInstances`` bloquea un nuevo anuncio.
    """
    try:
        import dbus
    except ImportError:
        logger.debug("dbus no disponible para limpieza BlueZ")
        return 0

    from bluezero import constants
    from bluezero import dbus_tools

    removed = 0
    try:
        bus = dbus.SystemBus()
        om = dbus.Interface(
            bus.get_object(BLUEZ_SERVICE, "/"),
            DBUS_OM_IFACE,
        )
        objects = om.GetManagedObjects()
    except Exception as exc:
        logger.debug("No se pudo listar objetos BlueZ: %s", exc)
        return 0

    adapter_path = None
    if adapter_address:
        try:
            adapter_path = dbus_tools.get_dbus_path(adapter=adapter_address)
        except Exception:
            adapter_path = None

    gatt_manager = None
    ad_manager = None
    if adapter_path:
        try:
            gatt_obj = bus.get_object(BLUEZ_SERVICE, adapter_path)
            gatt_manager = dbus.Interface(gatt_obj, GATT_MANAGER_IFACE)
            ad_manager = dbus.Interface(gatt_obj, LE_ADVERTISING_MANAGER_IFACE)
        except Exception as exc:
            logger.debug("Managers BlueZ no disponibles: %s", exc)

    for path, interfaces in objects.items():
        path_str = str(path)
        is_bluezero = (
            constants.BLUEZERO_DBUS_OBJECT in path_str
            or "bluezero" in path_str.lower()
            or "ukbaz" in path_str.lower()
        )
        if GATT_APP_IFACE in interfaces and gatt_manager is not None:
            if aggressive or is_bluezero:
                try:
                    gatt_manager.UnregisterApplication(path)
                    removed += 1
                    logger.info("GATT application huérfana desregistrada: %s", path_str)
                except Exception as exc:
                    logger.debug("UnregisterApplication %s: %s", path_str, exc)

        if LE_ADVERT_IFACE in interfaces and ad_manager is not None:
            if aggressive or is_bluezero:
                try:
                    ad_manager.UnregisterAdvertisement(path)
                    removed += 1
                    logger.info("LE advertisement huérfano desregistrado: %s", path_str)
                except Exception as exc:
                    logger.debug("UnregisterAdvertisement %s: %s", path_str, exc)

    return removed


def read_bluez_experimental_enabled() -> bool | None:
    """Lee ``Experimental=true`` de ``/etc/bluetooth/main.conf`` si existe."""
    conf_path = "/etc/bluetooth/main.conf"
    try:
        with open(conf_path, encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped.lower().startswith("experimental="):
                    value = stripped.split("=", 1)[1].strip().lower()
                    return value in {"1", "true", "yes", "on"}
    except OSError:
        return None
    return None


def diagnose_bluetooth_visibility(*, adapter_address: str | None = None) -> dict[str, Any]:
    """Informe operativo: discoverable vs anuncio LE real."""
    adapter = read_le_advertising_state()
    experimental = read_bluez_experimental_enabled()
    return {
        "adapter": adapter,
        "le_advertising_active": adapter.get("le_advertising_active"),
        "visible_for_ble_scan": bool(adapter.get("le_advertising_active")),
        "bluez_experimental": experimental,
        "note": (
            "Discoverable=yes solo afecta emparejamiento clásico; "
            "Web Bluetooth y escáneres BLE requieren LE Advertising activo."
        ),
        "hint": (
            "Si RegisterAdvertisement falla, verifica Experimental=true y "
            "KernelExperimental=true en /etc/bluetooth/main.conf y reinicia bluetooth."
            if experimental is False
            else None
        ),
    }
