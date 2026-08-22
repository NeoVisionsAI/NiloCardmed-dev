"""Punto de entrada principal de NiloCardmed."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from nilocardmed import __version__
from nilocardmed.camera.cli import run_camera_cli
from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import EnvironmentSettings
from nilocardmed.logging_setup import setup_logging
from nilocardmed.sampler.engine import SamplerEngine
from nilocardmed.sampler.cli import run_sampler_cli
from nilocardmed.sampler.supervisor import SamplerThreadSupervisor
from nilocardmed.ser_client.cli import run_ser_cli
from nilocardmed.wifi.cli import run_wifi_cli
from nilocardmed.wifi.exceptions import WifiError
from nilocardmed.wifi.service import WifiService
from nilocardmed.bluetooth.cli import run_bluetooth_cli
from nilocardmed.bluetooth.service import BluetoothService
from nilocardmed.bluetooth.supervisor import BluetoothSupervisor
from nilocardmed.http.server import HttpProvisioningService
from nilocardmed.cardmed.cli import run_cardmed_cli
from nilocardmed.resilience.cli import run_health_cli
from nilocardmed.resilience.supervisor import ResilienceSupervisor
from nilocardmed.operations_log import trace_system
from nilocardmed.storage.manager import StorageManager
from nilocardmed.system.watchdog import Watchdog
from nilocardmed.telemetry.store import telemetry

logger = logging.getLogger(__name__)


def run_daemon() -> int:
    """Arranca la aplicación con muestreo periódico en background."""
    env = EnvironmentSettings()
    setup_logging(
        level=env.log_level,
        structured=env.log_structured,
        log_dir=env.log_dir,
    )

    logger.info("Iniciando NiloCardmed v%s", __version__)
    logger.info("Entorno: %s", env.public_summary())
    trace_system(event="arranque", version=__version__)

    telemetry.configure_persistence(env.data_dir / "telemetry.jsonl")
    telemetry.load_recent_from_disk()

    config_manager = ConfigManager(env)
    config = config_manager.load()
    config_manager.save(config)

    logger.info("Configuración activa: %s", config_manager.summary())

    if config.storage.enabled and config.storage.retry_pending_on_startup:
        storage = StorageManager(
            config.storage,
            env,
            captures_dir=_captures_dir(config, env),
        )
        pending_result = storage.upload_pending_batch(config, window_active=False, max_items=1)
        if pending_result.get("uploaded", 0) > 0:
            logger.info("Reintento pending al arranque: %s subida(s)", pending_result["uploaded"])

    if (
        config.wifi.enabled
        and config.wifi.auto_connect_on_startup
        and config.wifi.ssid
    ):
        try:
            wifi_status = WifiService(config.wifi, config_manager=config_manager).connect_configured()
            logger.info(
                "WiFi auto-conectado ssid=%s ip=%s",
                wifi_status.ssid,
                wifi_status.ip_address,
            )
        except WifiError as exc:
            logger.warning("Auto-conexión WiFi fallida: %s", exc)

    shutdown = threading.Event()

    def _handle_signal(signum: int, _frame: object) -> None:
        logger.info("Señal recibida (%s); iniciando apagado", signum)
        shutdown.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    sampler_thread: threading.Thread | None = None
    sampler_engine: SamplerEngine | None = None
    sampler_thread_supervisor: SamplerThreadSupervisor | None = None

    if config.sampling.enabled:
        sampler_engine = SamplerEngine(config_manager, env)

        def _start_sampler_thread() -> threading.Thread:
            def _run_sampler() -> None:
                try:
                    assert sampler_engine is not None
                    sampler_engine.run(shutdown)
                except Exception:
                    logger.exception("Error fatal en el hilo de muestreo")

            thread = threading.Thread(target=_run_sampler, name="sampler", daemon=True)
            thread.start()
            return thread

        sampler_thread = _start_sampler_thread()
        sampler_thread_supervisor = SamplerThreadSupervisor(
            config_manager,
            sampler_engine=sampler_engine,
            start_sampler=_start_sampler_thread,
        )
        sampler_thread_supervisor.attach_thread(sampler_thread)
        sampler_thread_supervisor.start(shutdown)
        logger.info("Motor de muestreo iniciado en background")
    else:
        logger.info("Muestreo deshabilitado; servicio en modo espera")

    resilience_supervisor: ResilienceSupervisor | None = None
    if config.resilience.enabled:
        resilience_supervisor = ResilienceSupervisor(
            config_manager,
            env,
            sampler_engine=sampler_engine,
        )
        resilience_supervisor.start(shutdown)
        logger.info("Supervisor de resiliencia iniciado")

    watchdog: Watchdog | None = None
    if config.resilience.enabled and config.resilience.watchdog_enabled and config.sampling.enabled:
        watchdog = Watchdog(config_manager, sampler_engine=sampler_engine)
        watchdog.start(shutdown)
        logger.info("Watchdog de muestreo iniciado")

    bluetooth_service: BluetoothService | None = None
    bluetooth_supervisor: BluetoothSupervisor | None = None
    if config.bluetooth.enabled:
        bluetooth_service = BluetoothService(config.bluetooth, config_manager, env)
        bluetooth_service.start(shutdown)
        logger.info(
            "Servicio Bluetooth iniciado (backend=%s, name=%s)",
            bluetooth_service.backend.name,
            config.bluetooth.device_name,
        )
        if config.resilience.enabled and (
            config.resilience.bluetooth_supervisor_enabled
            or config.resilience.bluetooth_keep_discoverable_enabled
        ):
            bluetooth_supervisor = BluetoothSupervisor(config_manager, bluetooth_service)
            bluetooth_supervisor.start(shutdown)
            logger.info(
                "Supervisor Bluetooth iniciado (discoverable=%s, reinicio_gatt=%s)",
                config.resilience.bluetooth_keep_discoverable_enabled,
                config.resilience.bluetooth_supervisor_enabled,
            )
    else:
        logger.info("Bluetooth deshabilitado")

    http_service: HttpProvisioningService | None = None
    if config.http.enabled:
        http_service = HttpProvisioningService(
            config.http,
            config_manager,
            env,
            config.bluetooth,
        )
        http_service.start(shutdown)
        logger.info(
            "Servidor HTTP de aprovisionamiento activo (puerto=%s, ap_ip=%s)",
            config.http.port,
            config.http.ap_ip if config.http.bind_ap_only else config.http.host,
        )
    else:
        logger.info("HTTP de aprovisionamiento deshabilitado")

    while not shutdown.is_set():
        shutdown.wait(timeout=1)

    if sampler_thread is not None:
        sampler_thread.join(timeout=30)
        if sampler_thread.is_alive():
            logger.warning("El hilo de muestreo no terminó a tiempo")

    if sampler_thread_supervisor is not None:
        sampler_thread_supervisor.join(timeout=5)

    if resilience_supervisor is not None:
        resilience_supervisor.join(timeout=10)

    if watchdog is not None:
        watchdog.join(timeout=5)

    if bluetooth_supervisor is not None:
        bluetooth_supervisor.join(timeout=5)

    if bluetooth_service is not None:
        bluetooth_service.stop(shutdown)

    if http_service is not None:
        http_service.stop()

    logger.info("NiloCardmed detenido correctamente")
    return 0


def _captures_dir(config, env: EnvironmentSettings):
    from pathlib import Path

    if config.camera.capture_dir:
        return Path(config.camera.capture_dir)
    return env.data_dir / "captures"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NiloCardmed")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="Ejecutar servicio (por defecto)")

    camera = sub.add_parser("camera", help="Herramientas de cámara USB")
    camera_sub = camera.add_subparsers(dest="camera_command", required=True)
    camera_sub.add_parser("list", help="Listar cámaras detectadas")
    camera_sub.add_parser("capture-test", help="Capturar imagen de prueba")

    ser = sub.add_parser("ser", help="Cliente HTTP hacia SER")
    ser_sub = ser.add_subparsers(dest="ser_command", required=True)
    ser_sub.add_parser("send-test", help="Enviar imagen de prueba a SER")

    sampler = sub.add_parser("sampler", help="Motor de muestreo periódico")
    sampler_sub = sampler.add_subparsers(dest="sampler_command", required=True)
    sampler_sub.add_parser("status", help="Estado de ventana y muestreo")
    sampler_sub.add_parser("run-once", help="Un ciclo captura + envío")
    sampler_sub.add_parser("run", help="Bucle de muestreo en primer plano")

    wifi = sub.add_parser("wifi", help="Gestión WiFi")
    wifi_sub = wifi.add_subparsers(dest="wifi_command", required=True)
    wifi_sub.add_parser("scan", help="Escanear redes")
    wifi_sub.add_parser("status", help="Estado de conexión")
    wifi_sub.add_parser("connect", help="Conectar (usar wifi connect --ssid)")
    wifi_sub.add_parser("disconnect", help="Desconectar")
    wifi_sub.add_parser("test", help="Probar conectividad")

    bluetooth = sub.add_parser("bluetooth", help="Servicio BLE/GATT")
    bt_sub = bluetooth.add_subparsers(dest="bluetooth_command", required=True)
    bt_sub.add_parser("run", help="Servidor GATT en primer plano")
    bt_sub.add_parser("test-session", help="Probar auth+ping (mock)")
    bt_sub.add_parser("info", help="UUIDs y configuración BLE")
    bt_sub.add_parser("diag", help="Diagnóstico discoverable vs anuncio LE")

    cardmed = sub.add_parser("cardmed", help="Configuración y prueba CardMed")
    cardmed_sub = cardmed.add_subparsers(dest="cardmed_command", required=True)
    cardmed_sub.add_parser("get", help="Ver configuración CardMed")
    cardmed_sub.add_parser("configure", help="Configurar CardMed")
    cardmed_sub.add_parser("test", help="Prueba end-to-end CardMed")

    health = sub.add_parser("health", help="Salud y resiliencia")
    health_sub = health.add_subparsers(dest="health_command", required=True)
    health_sub.add_parser("status", help="Informe JSON de salud")
    health_sub.add_parser("check", help="Comprobar salud (exit code)")

    return parser


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "camera":
        sys.exit(run_camera_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "ser":
        sys.exit(run_ser_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "sampler":
        sys.exit(run_sampler_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "wifi":
        sys.exit(run_wifi_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "bluetooth":
        sys.exit(run_bluetooth_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "cardmed":
        sys.exit(run_cardmed_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == "health":
        sys.exit(run_health_cli(sys.argv[2:]))

    parser = _build_parser()
    args = parser.parse_args()

    if args.command in (None, "run"):
        sys.exit(run_daemon())

    parser.error(f"Comando no reconocido: {args.command}")


if __name__ == "__main__":
    main()
