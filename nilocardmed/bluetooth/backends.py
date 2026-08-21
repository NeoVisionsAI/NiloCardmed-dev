"""Backends BLE/GATT."""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable

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
        self._local_stop = threading.Event()

    @abstractmethod
    def start(self, shutdown: threading.Event) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    def is_healthy(self) -> bool:
        """Indica si el backend sigue publicando GATT."""
        return True

    def _should_stop(self, shutdown: threading.Event) -> bool:
        return shutdown.is_set() or self._local_stop.is_set()

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
        self._publish_thread: threading.Thread | None = None
        self._tx_characteristic = None
        self._last_response = bytearray()
        self._peripheral = None
        self._gatt_registered = threading.Event()
        self._advert_registered = threading.Event()
        self._master_shutdown: threading.Event | None = None
        self._started_at: float | None = None
        self._lifecycle_lock = threading.Lock()

    def start(self, shutdown: threading.Event) -> None:
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                return

            self._local_stop.clear()
            self._gatt_registered.clear()
            self._advert_registered.clear()
            self._started_at = time.monotonic()
            self._master_shutdown = shutdown

            def _runner() -> None:
                try:
                    self._run_peripheral(shutdown)
                except Exception:
                    logger.exception("Error en backend BlueZ (se reintentará vía supervisor)")
                finally:
                    self._gatt_registered.clear()
                    self._advert_registered.clear()
                    self._publish_thread = None
                    self._tx_characteristic = None
                    peripheral_ref = self._peripheral
                    self._peripheral = None
                    self._shutdown_peripheral(peripheral_ref)
                    self._started_at = None

            self._thread = threading.Thread(target=_runner, name="bluetooth-bluez", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._local_stop.set()
            self._shutdown_peripheral(self._peripheral)
            if self._publish_thread and self._publish_thread.is_alive():
                self._publish_thread.join(timeout=15)
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=15)
            self._thread = None
            self._publish_thread = None
            self._tx_characteristic = None
            self._peripheral = None
            self._gatt_registered.clear()
            self._advert_registered.clear()
            self._started_at = None
            self._local_stop.clear()

    def has_active_client(self) -> bool:
        return self._tx_characteristic is not None

    def is_healthy(self) -> bool:
        if self._thread is None or not self._thread.is_alive():
            return False
        if self._publish_thread is not None and not self._publish_thread.is_alive():
            return False
        if self.has_active_client():
            return True
        if not self._gatt_registered.is_set() or not self._advert_registered.is_set():
            if self._started_at is not None and time.monotonic() - self._started_at < 45.0:
                return True
            return False
        return True

    @staticmethod
    def _wait_registration_event(
        ready: threading.Event,
        failed: threading.Event,
        stop_check: Callable[[], bool],
        *,
        timeout: float = 20.0,
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ready.is_set():
                return True
            if failed.is_set():
                return False
            if stop_check():
                return False
            time.sleep(0.05)
        return False

    def _purge_stale_advertisement(self, ble) -> None:
        try:
            ble.ad_manager.unregister_advertisement(ble.advert)
        except Exception as exc:
            logger.debug("purge unregister_advertisement: %s", exc)
        try:
            ble.advert.remove_from_connection()
        except Exception as exc:
            logger.debug("purge remove advert: %s", exc)

    def _publish_peripheral(self, ble, shutdown: threading.Event) -> None:
        """Publica GATT + LE advertisement esperando confirmación de BlueZ."""
        import dbus

        for service in ble.services:
            ble.app.add_managed_object(service)
        for characteristic in ble.characteristics:
            ble.app.add_managed_object(characteristic)
        for descriptor in ble.descriptors:
            ble.app.add_managed_object(descriptor)
        ble._create_advertisement()

        if not ble.dongle.powered:
            ble.dongle.powered = True
        try:
            ble.dongle.discoverable = True
        except Exception as exc:
            logger.warning("No se pudo marcar adaptador discoverable: %s", exc)

        self._purge_stale_advertisement(ble)

        gatt_ready = threading.Event()
        gatt_failed = threading.Event()
        advert_ready = threading.Event()
        advert_failed = threading.Event()
        failure_messages: list[str] = []

        def _on_gatt_ok() -> None:
            logger.info("GATT application registered")
            gatt_ready.set()

        def _on_gatt_err(error) -> None:
            message = f"Failed to register GATT application: {error}"
            failure_messages.append(message)
            logger.error(message)
            gatt_failed.set()

        def _on_advert_ok() -> None:
            logger.info("BLE advertisement registrado")
            advert_ready.set()

        def _on_advert_err(error) -> None:
            message = f"Failed to register advertisement: {error}"
            failure_messages.append(message)
            logger.error(message)
            advert_failed.set()
            try:
                ble.mainloop.quit()
            except Exception:
                pass

        ble.srv_mng.manager_methods.RegisterApplication(
            ble.app.get_path(),
            dbus.Dictionary({}, signature="sv"),
            reply_handler=_on_gatt_ok,
            error_handler=_on_gatt_err,
        )

        if not self._wait_registration_event(
            gatt_ready,
            gatt_failed,
            lambda: self._should_stop(shutdown),
            timeout=20.0,
        ):
            detail = failure_messages[-1] if failure_messages else "timeout registrando GATT"
            raise BluetoothBackendError(detail)

        ble.ad_manager.advert_mngr_methods.RegisterAdvertisement(
            ble.advert.get_path(),
            dbus.Dictionary({}, signature="sv"),
            reply_handler=_on_advert_ok,
            error_handler=_on_advert_err,
        )

        if not self._wait_registration_event(
            advert_ready,
            advert_failed,
            lambda: self._should_stop(shutdown),
            timeout=20.0,
        ):
            self._shutdown_peripheral(ble)
            detail = failure_messages[-1] if failure_messages else "timeout registrando advertisement"
            raise BluetoothBackendError(detail)

        self._gatt_registered.set()
        self._advert_registered.set()
        from nilocardmed.operations_log import trace_system

        trace_system(
            event="bluetooth_activo",
            detail="GATT y advertisement publicados",
            device_name=self.settings.device_name,
            adapter=ble.dongle.address,
        )
        logger.info("GATT BLE registrado y publicando (GATT + advertisement OK)")

        try:
            ble.mainloop.run()
        finally:
            self._gatt_registered.clear()
            self._advert_registered.clear()

    @staticmethod
    def _shutdown_peripheral(ble) -> None:
        """Desregistra GATT/advertisement y libera rutas D-Bus de bluezero."""
        if ble is None:
            return

        try:
            ble.mainloop.quit()
        except Exception as exc:
            logger.debug("mainloop.quit: %s", exc)

        try:
            ble.ad_manager.unregister_advertisement(ble.advert)
        except Exception as exc:
            logger.debug("unregister_advertisement: %s", exc)

        try:
            ble.srv_mng.unregister_application(ble.app)
        except Exception as exc:
            logger.debug("unregister_application: %s", exc)

        managed = list(getattr(ble.app, "managed_objs", []))
        for obj in reversed(managed):
            try:
                obj.remove_from_connection()
            except Exception as exc:
                logger.debug("remove managed obj: %s", exc)

        try:
            ble.app.remove_from_connection()
        except Exception as exc:
            logger.debug("remove application: %s", exc)

        try:
            ble.advert.remove_from_connection()
        except Exception as exc:
            logger.debug("remove advertisement: %s", exc)

        time.sleep(0.5)

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
        try:
            adapter.discoverable = True
        except Exception as exc:
            logger.warning("No se pudo marcar adaptador discoverable al arrancar: %s", exc)
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

        publish_thread = threading.Thread(
            target=self._publish_peripheral,
            args=(ble, shutdown),
            name="bluezero-publish",
            daemon=True,
        )
        self._publish_thread = publish_thread
        publish_thread.start()

        registration_deadline = time.monotonic() + 45.0
        while time.monotonic() < registration_deadline:
            if self._should_stop(shutdown):
                return
            if self._gatt_registered.is_set() and self._advert_registered.is_set():
                break
            if not publish_thread.is_alive():
                raise BluetoothBackendError("Publicación BLE terminó antes de completar registro")
            time.sleep(0.2)

        if (
            not self._gatt_registered.is_set() or not self._advert_registered.is_set()
        ) and not self._should_stop(shutdown):
            raise BluetoothBackendError("Timeout esperando GATT + advertisement")

        while not self._should_stop(shutdown):
            if not publish_thread.is_alive():
                logger.error("Hilo publish GATT terminó inesperadamente")
                self._gatt_registered.clear()
                self._advert_registered.clear()
                break
            shutdown.wait(timeout=1.0)

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
