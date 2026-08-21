"""Backends BLE/GATT."""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from abc import ABC, abstractmethod

from nilocardmed.bluetooth.exceptions import BluetoothBackendError, BluetoothConfigError
from nilocardmed.bluetooth.framing import BleTransport
from nilocardmed.bluetooth.protocol import CommandRouter
from nilocardmed.config.models import BluetoothSettings
from nilocardmed.operations_log import trace_ble_client

logger = logging.getLogger(__name__)


class BluetoothBackend(ABC):
    """Interfaz para publicar el servicio GATT."""

    name: str

    def __init__(self, settings: BluetoothSettings, router: CommandRouter) -> None:
        self.settings = settings
        self.router = router
        self._transport = BleTransport(router, settings)

    @abstractmethod
    def start(self, shutdown: threading.Event) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    def process_frames(self, raw: bytes | str) -> list[bytes]:
        data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        return self._transport.handle_write(data)

    def process(self, raw: bytes | str) -> bytes:
        """Procesa un write RX y devuelve la respuesta (frames unidos si hay varios)."""
        frames = self.process_frames(raw)
        if not frames:
            return b""
        if len(frames) == 1:
            return frames[0]
        return b"\n".join(frames)


class MockBluetoothBackend(BluetoothBackend):
    """Backend en memoria para pruebas del protocolo sin hardware BLE."""

    name = "mock"

    def start(self, shutdown: threading.Event) -> None:
        logger.info("Backend Bluetooth mock en ejecución (sin GATT real)")
        while not shutdown.wait(timeout=1):
            pass

    def stop(self) -> None:
        logger.info("Backend Bluetooth mock detenido")


class BluezBluetoothBackend(BluetoothBackend):
    """Backend BlueZ usando la librería bluezero (Peripheral GATT)."""

    name = "bluez"

    def __init__(self, settings: BluetoothSettings, router: CommandRouter) -> None:
        super().__init__(settings, router)
        self._thread: threading.Thread | None = None
        self._tx_characteristic = None
        self._last_response = bytearray()
        self._peripheral = None

    def start(self, shutdown: threading.Event) -> None:
        if self._thread and self._thread.is_alive():
            return

        def _runner() -> None:
            try:
                self._run_peripheral(shutdown)
            except Exception:
                logger.exception("Error en backend BlueZ")
                shutdown.set()

        self._thread = threading.Thread(target=_runner, name="bluetooth-bluez", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run_peripheral(self, shutdown: threading.Event) -> None:
        try:
            from bluezero import adapter as bz_adapter
            from bluezero import peripheral
        except ImportError as exc:
            missing = str(exc)
            if "gi" in missing or "GLib" in missing:
                hint = "PyGObject (gi) no disponible — rebuild imagen Docker con libgirepository"
            else:
                hint = "Dependencias BLE incompletas (bluezero/dbus-python/PyGObject)"
            raise BluetoothBackendError(f"{hint}: {exc}") from exc

        if self.settings.dbus_system_bus_address:
            os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = self.settings.dbus_system_bus_address

        adapter_address = self._resolve_adapter_address(bz_adapter)
        adapter = bz_adapter.Adapter(adapter_address)
        if adapter.alias != self.settings.device_name:
            adapter.alias = self.settings.device_name
            logger.info(
                "Alias BlueZ del adaptador → %s (antes: hostname del sistema)",
                self.settings.device_name,
            )

        logger.info(
            "Publicando GATT BLE name=%s adapter=%s service=%s framing=%s",
            self.settings.device_name,
            adapter_address,
            self.settings.service_uuid,
            self.settings.ble_framing_enabled,
        )
        from nilocardmed.operations_log import trace_system

        trace_system(
            event="bluetooth_activo",
            detail="GATT publicado",
            device_name=self.settings.device_name,
            adapter=adapter_address,
        )

        ble = peripheral.Peripheral(
            adapter_address,
            local_name=self.settings.device_name,
            appearance=self.settings.appearance,
        )
        self._peripheral = ble

        ble.add_service(srv_id=1, uuid=self.settings.service_uuid, primary=True)
        ble.add_characteristic(
            srv_id=1,
            chr_id=1,
            uuid=self.settings.rx_characteristic_uuid,
            value=[],
            notifying=False,
            flags=["write", "write-without-response"],
            write_callback=self._on_write,
        )
        ble.add_characteristic(
            srv_id=1,
            chr_id=2,
            uuid=self.settings.tx_characteristic_uuid,
            value=[],
            notifying=False,
            flags=["read", "notify"],
            read_callback=self._on_read,
            notify_callback=self._on_notify,
        )

        publish_thread = threading.Thread(target=ble.publish, name="bluezero-publish", daemon=True)
        publish_thread.start()

        while not shutdown.wait(timeout=1):
            pass

        logger.info("Apagado solicitado; deteniendo publicación BLE")

    def _resolve_adapter_address(self, bz_adapter) -> str:
        if self.settings.adapter_address:
            return self.settings.adapter_address
        adapters = bz_adapter.list_adapters()
        if not adapters:
            raise BluetoothBackendError("No se detectaron adaptadores Bluetooth")
        for address in adapters:
            name = bz_adapter.Adapter(address).name
            if name == self.settings.adapter:
                return address
        return adapters[0]

    def _on_write(self, value, _options) -> None:
        frames = self.process_frames(bytes(value))
        if not frames:
            return

        try:
            self._last_response = bytearray(self._transport.full_response_from_frames(frames))
        except Exception:
            self._last_response = bytearray(frames[-1])

        if self._tx_characteristic is None:
            return

        delay_s = self.settings.ble_inter_frame_delay_ms / 1000.0
        for index, frame in enumerate(frames):
            self._tx_characteristic.set_value(list(frame))
            self._emit_notify()
            if index + 1 < len(frames) and delay_s > 0:
                time.sleep(delay_s)

    def _emit_notify(self) -> None:
        characteristic = self._tx_characteristic
        if characteristic is None:
            return
        for method_name in ("send_notification", "notify"):
            method = getattr(characteristic, method_name, None)
            if callable(method):
                try:
                    method()
                    return
                except Exception as exc:
                    logger.debug("Notify via %s falló: %s", method_name, exc)

    def _on_read(self) -> list[int]:
        return list(self._last_response)

    def _on_notify(self, notifying, characteristic) -> None:
        if notifying:
            self._tx_characteristic = characteristic
            logger.info("Cliente BLE suscrito a notificaciones TX")
            trace_ble_client(event="conectado", detail="suscrito a notificaciones TX")
        else:
            self._tx_characteristic = None
            logger.info("Cliente BLE canceló notificaciones TX")
            trace_ble_client(event="desconectado", detail="canceló notificaciones TX")


def select_backend(settings: BluetoothSettings, router: CommandRouter) -> BluetoothBackend:
    backend = settings.backend
    if backend == "mock":
        return MockBluetoothBackend(settings, router)
    if backend == "bluez":
        return BluezBluetoothBackend(settings, router)
    if backend == "auto":
        if shutil.which("bluetoothctl") or os.path.exists("/var/run/dbus/system_bus_socket"):
            try:
                import dbus  # noqa: F401
                import gi

                gi.require_version("GLib", "2.0")
                from gi.repository import GLib  # noqa: F401
                import bluezero  # noqa: F401

                logger.debug("Backend Bluetooth auto -> bluez")
                return BluezBluetoothBackend(settings, router)
            except ImportError as exc:
                logger.warning("BlueZ detectado pero dependencias BLE no instaladas (%s); usando mock", exc)
        logger.warning("Backend Bluetooth auto -> mock")
        return MockBluetoothBackend(settings, router)
    raise BluetoothConfigError(f"Backend Bluetooth no soportado: {backend}")
