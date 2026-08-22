"""Backends BLE/GATT."""

from __future__ import annotations

import logging
import os
import queue
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

# AD legacy 31 bytes: flags (~3) + Complete Local Name (2 + utf8). Sin UUID ni Appearance.
MAX_LE_AD_LOCAL_NAME_BYTES = 24


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
        self._command_queue: queue.Queue[bytes | None] = queue.Queue()
        self._command_worker: threading.Thread | None = None
        self._command_worker_stop = threading.Event()
        self._mainloop_tx_queue: queue.Queue[list[bytes]] = queue.Queue()
        self._mainloop_action_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._last_le_advert_refresh_at: float = 0.0
        self._gatt_client_connected: bool = False

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
            self._stop_command_worker()

    def _start_command_worker(self, shutdown: threading.Event) -> None:
        if self._command_worker and self._command_worker.is_alive():
            return

        self._command_worker_stop.clear()

        def _worker() -> None:
            while not self._command_worker_stop.is_set():
                try:
                    payload = self._command_queue.get(timeout=0.3)
                except queue.Empty:
                    if self._should_stop(shutdown):
                        break
                    continue
                if payload is None:
                    break
                try:
                    self._process_command_write(payload)
                except Exception:
                    logger.exception("Error procesando comando BLE")

        self._command_worker = threading.Thread(
            target=_worker,
            name="ble-command-worker",
            daemon=True,
        )
        self._command_worker.start()

    def _stop_command_worker(self) -> None:
        self._command_worker_stop.set()
        try:
            self._command_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._command_worker and self._command_worker.is_alive():
            self._command_worker.join(timeout=30)
        self._command_worker = None
        while True:
            try:
                self._command_queue.get_nowait()
            except queue.Empty:
                break

    def _process_command_write(self, data: bytes) -> None:
        frames = self.process_frames(data)
        if not frames:
            return

        try:
            self._last_response = bytearray(self._transport.full_response_from_frames(frames))
        except Exception:
            self._last_response = bytearray(frames[-1])

        if self._tx_characteristic is None:
            return

        self._schedule_tx_delivery(frames)

    def _schedule_tx_delivery(self, frames: list[bytes]) -> None:
        self._mainloop_tx_queue.put(frames)

    def _poll_tx_responses(self) -> bool:
        try:
            while True:
                frames = self._mainloop_tx_queue.get_nowait()
                self._deliver_response_frames(frames)
        except queue.Empty:
            pass
        return True

    def _deliver_response_frames(self, frames: list[bytes]) -> bool:
        if self._tx_characteristic is None:
            return False

        delay_s = self.settings.ble_inter_frame_delay_ms / 1000.0
        for index, frame in enumerate(frames):
            self._tx_characteristic.set_value(list(frame))
            self._emit_notify()
            if index + 1 < len(frames) and delay_s > 0:
                time.sleep(delay_s)
        return False

    def has_active_client(self) -> bool:
        # GATT conectado != suscripción notify; el supervisor no debe refrescar anuncio entre ambos.
        return self._gatt_client_connected or self._tx_characteristic is not None

    def is_publish_alive(self) -> bool:
        return self._publish_thread is not None and self._publish_thread.is_alive()

    def request_advertising_refresh(self) -> bool:
        """Pide re-registrar LE advertising en el hilo del mainloop GLib."""
        if not self.is_publish_alive():
            return False
        self._mainloop_action_queue.put(self._refresh_le_advertising)
        try:
            import gi

            gi.require_version("GLib", "2.0")
            from gi.repository import GLib

            GLib.idle_add(self._run_pending_mainloop_actions_once)
        except Exception as exc:
            logger.debug("No se pudo programar refresh LE en mainloop: %s", exc)
        return True

    def _run_pending_mainloop_actions_once(self) -> bool:
        self._run_pending_mainloop_actions()
        return False

    def is_healthy(self) -> bool:
        if self._thread is None or not self._thread.is_alive():
            return False
        if not self.is_publish_alive():
            return False
        if not self._gatt_registered.is_set() or not self._advert_registered.is_set():
            if self._started_at is not None and time.monotonic() - self._started_at < 45.0:
                return True
            return False
        # GATT + anuncio registrados en BlueZ: confiar en el registro. bluetoothctl
        # desde el contenedor suele reportar ActiveInstances=0 con anuncio activo.
        return True

    def _prepare_le_advertisement(self, ble, *, recreate: bool = False) -> None:
        from nilocardmed.bluetooth.advertising_status import (
            purge_stale_bluez_registrations,
            unregister_le_advertisement_path,
        )

        adapter_address = getattr(ble.dongle, "address", None)
        advert_path = str(ble.advert.get_path())
        self._purge_stale_advertisement(ble)
        unregister_le_advertisement_path(advert_path, adapter_address=adapter_address)
        purge_stale_bluez_registrations(adapter_address, aggressive=True)
        if recreate:
            try:
                ble.advert.remove_from_connection()
            except Exception as exc:
                logger.debug("remove advert antes de recrear: %s", exc)
            ble._create_advertisement()
        self._configure_le_advertisement(ble, self.settings.device_name)
        time.sleep(0.5)

    @staticmethod
    def _configure_le_advertisement(ble, local_name: str) -> None:
        """Fuerza Complete Local Name (AD type 0x09) para Web Bluetooth namePrefix."""
        from bluezero import constants

        name = (local_name or "").strip()
        if not name:
            raise BluetoothBackendError(
                "device_name vacío: Web Bluetooth requiere LocalName en el anuncio LE"
            )
        encoded_len = len(name.encode("utf-8"))
        if encoded_len > MAX_LE_AD_LOCAL_NAME_BYTES:
            logger.warning(
                "LocalName BLE largo (%s bytes); puede no caber en AD legacy de 31 bytes",
                encoded_len,
            )

        ble.advert.service_UUIDs = []
        try:
            ble.advert.props[constants.LE_ADVERTISEMENT_IFACE]["Appearance"] = None
        except Exception as exc:
            logger.debug("Anuncio: no se pudo omitir Appearance: %s", exc)
        try:
            ble.advert.include_tx_power = False
        except Exception as exc:
            logger.debug("Anuncio: no se pudo omitir tx-power: %s", exc)
        ble.advert.local_name = name
        logger.info(
            "Anuncio LE: LocalName=%r (Complete Local Name para Web Bluetooth)",
            name,
        )

    def _register_le_advertisement(
        self,
        ble,
        *,
        on_ok: Callable[[], None],
        on_err: Callable[[object], None],
        prepare: bool = False,
        recreate_on_prepare: bool = False,
    ) -> None:
        import dbus

        from nilocardmed.bluetooth.advertising_status import is_le_advertisement_registered

        if prepare:
            self._prepare_le_advertisement(ble, recreate=recreate_on_prepare)

        advert_path = str(ble.advert.get_path())

        def _err_handler(error) -> None:
            error_text = str(error)
            if "AlreadyExists" in error_text:
                if is_le_advertisement_registered(advert_path):
                    logger.info("LE advertisement ya registrado en BlueZ (%s)", advert_path)
                    on_ok()
                    return
                try:
                    from nilocardmed.bluetooth.advertising_status import read_le_advertising_state

                    le_state = read_le_advertising_state(timeout=2.0)
                    if le_state.get("le_advertising_active"):
                        logger.info(
                            "BlueZ AlreadyExists con LE activo (ActiveInstances=%s); continuando",
                            le_state.get("active_instances"),
                        )
                        on_ok()
                        return
                except Exception as exc:
                    logger.debug("Comprobación LE tras AlreadyExists: %s", exc)
            on_err(error)

        ble.ad_manager.advert_mngr_methods.RegisterAdvertisement(
            advert_path,
            dbus.Dictionary({}, signature="sv"),
            reply_handler=on_ok,
            error_handler=_err_handler,
        )

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

        from nilocardmed.bluetooth.advertising_status import purge_stale_bluez_registrations

        for service in ble.services:
            ble.app.add_managed_object(service)
        for characteristic in ble.characteristics:
            ble.app.add_managed_object(characteristic)
        for descriptor in ble.descriptors:
            ble.app.add_managed_object(descriptor)
        ble._create_advertisement()
        self._configure_le_advertisement(ble, self.settings.device_name)

        if not ble.dongle.powered:
            ble.dongle.powered = True
        try:
            ble.dongle.discoverable = True
        except Exception as exc:
            logger.warning("No se pudo marcar adaptador discoverable: %s", exc)

        self._purge_stale_advertisement(ble)
        purge_stale_bluez_registrations(
            getattr(ble.dongle, "address", None),
            aggressive=True,
        )

        failure_messages: list[str] = []
        advert_attempts = {"count": 0}

        def _mark_ble_active() -> None:
            from nilocardmed.operations_log import trace_system

            trace_system(
                event="bluetooth_activo",
                detail="GATT y advertisement publicados",
                device_name=self.settings.device_name,
                adapter=ble.dongle.address,
            )
            logger.info("GATT BLE registrado y publicando (GATT + advertisement OK)")

        def _register_advertisement(*, prepare: bool = False, recreate: bool = False) -> None:
            self._register_le_advertisement(
                ble,
                on_ok=_on_advert_ok,
                on_err=_on_advert_err,
                prepare=prepare,
                recreate_on_prepare=recreate,
            )

        def _on_gatt_ok() -> None:
            logger.info("GATT application registered")
            self._gatt_registered.set()
            _register_advertisement()

        def _on_gatt_err(error) -> None:
            message = f"Failed to register GATT application: {error}"
            failure_messages.append(message)
            logger.error(message)
            try:
                ble.mainloop.quit()
            except Exception:
                pass

        def _on_advert_ok() -> None:
            logger.info("BLE advertisement registrado")
            self._advert_registered.set()
            if self._gatt_registered.is_set():
                _mark_ble_active()

        def _on_advert_err(error) -> None:
            advert_attempts["count"] += 1
            message = f"Failed to register advertisement: {error}"
            if advert_attempts["count"] <= 3:
                logger.warning(
                    "%s; limpiando anuncios huérfanos y reintentando (%s/3)",
                    message,
                    advert_attempts["count"],
                )
                _register_advertisement(prepare=True, recreate=True)
                return

            failure_messages.append(message)
            logger.error(message)
            try:
                ble.srv_mng.unregister_application(ble.app)
            except Exception as exc:
                logger.debug("rollback unregister_application: %s", exc)
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

        self._start_command_worker(shutdown)

        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        def _periodic_ble_maintenance() -> bool:
            if self._should_stop(shutdown):
                return False
            self._run_pending_mainloop_actions()
            return True

        GLib.timeout_add_seconds(15, _periodic_ble_maintenance)
        GLib.timeout_add(50, self._poll_tx_responses)

        try:
            ble.mainloop.run()
        finally:
            self._stop_command_worker()
            self._gatt_registered.clear()
            self._advert_registered.clear()

        if failure_messages and not self._should_stop(shutdown):
            raise BluetoothBackendError(failure_messages[-1])

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

        try:
            from nilocardmed.bluetooth.advertising_status import purge_stale_bluez_registrations

            adapter_address = getattr(getattr(ble, "dongle", None), "address", None)
            removed = purge_stale_bluez_registrations(adapter_address, aggressive=True)
            if removed:
                logger.info("Limpieza BlueZ tras apagado: %s registro(s) eliminado(s)", removed)
        except Exception as exc:
            logger.debug("purge tras apagado BLE: %s", exc)

        time.sleep(0.5)

    def _run_pending_mainloop_actions(self) -> None:
        while True:
            try:
                action = self._mainloop_action_queue.get_nowait()
            except queue.Empty:
                break
            try:
                action()
            except Exception:
                logger.exception("Acción mainloop BLE fallida")

    def _refresh_le_advertising(self) -> None:
        ble = self._peripheral
        if ble is None or self.has_active_client():
            return

        now = time.monotonic()
        if now - self._last_le_advert_refresh_at < 30.0:
            return
        self._last_le_advert_refresh_at = now

        logger.info("Restaurando anuncio LE con LocalName=%r", self.settings.device_name)

        def _on_refresh_ok() -> None:
            self._advert_registered.set()
            logger.info("LE advertisement restaurado")

        def _on_refresh_err(error) -> None:
            logger.error("Restore advertisement failed: %s", error)

        self._register_le_advertisement(
            ble,
            on_ok=_on_refresh_ok,
            on_err=_on_refresh_err,
            prepare=True,
            recreate_on_prepare=True,
        )

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

        from nilocardmed.bluetooth.advertising_status import purge_stale_bluez_registrations

        removed = purge_stale_bluez_registrations(adapter_address, aggressive=True)
        if removed:
            logger.info("Limpieza BlueZ: %s registro(s) huérfano(s) eliminado(s)", removed)
            time.sleep(1.0)

        ble = peripheral.Peripheral(
            adapter_address,
            local_name=self.settings.device_name,
            appearance=None,
        )
        self._peripheral = ble

        def _handle_connect(*_args) -> None:
            self._gatt_client_connected = True
            logger.info("Cliente BLE conectado (GATT)")
            trace_ble_client(event="conectado", detail="GATT link up")

        def _handle_disconnect(*_args) -> None:
            self._gatt_client_connected = False
            self._tx_characteristic = None
            logger.info("Cliente BLE desconectado (adaptador)")
            trace_ble_client(event="desconectado", detail="desconexión del adaptador")
            self.request_advertising_refresh()

        try:
            ble.on_connect = _handle_connect
        except Exception as exc:
            logger.debug("on_connect no disponible: %s", exc)

        try:
            ble.on_disconnect = _handle_disconnect
        except Exception as exc:
            logger.debug("on_disconnect no disponible: %s", exc)

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
        self._gatt_client_connected = True
        try:
            self._command_queue.put_nowait(bytes(value))
        except queue.Full:
            logger.warning("Cola de comandos BLE llena; descartando write")

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
