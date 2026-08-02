"""CLI de gestión WiFi."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys

from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import EnvironmentSettings
from nilocardmed.logging_setup import setup_logging
from nilocardmed.wifi.exceptions import WifiError
from nilocardmed.wifi.service import WifiService

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Herramientas WiFi NiloCardmed")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="Escanear redes WiFi disponibles")

    sub.add_parser("status", help="Estado de conexión WiFi")

    connect = sub.add_parser("connect", help="Conectar a una red WiFi")
    connect.add_argument("--ssid", required=True)
    connect.add_argument("--password", help="Contraseña (prompt si falta en red segura)")
    connect.add_argument(
        "--no-persist",
        action="store_true",
        help="No guardar credenciales en config.json",
    )

    sub.add_parser("disconnect", help="Desconectar WiFi")
    sub.add_parser("test", help="Comprobar conectividad externa")

    return parser


def run_wifi_cli(argv: list[str] | None = None) -> int:
    env = EnvironmentSettings()
    setup_logging(
        level=env.log_level,
        structured=env.log_structured,
        log_dir=env.log_dir,
    )

    config_manager = ConfigManager(env)
    config = config_manager.load()
    service = WifiService(config.wifi, config_manager=config_manager)
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "scan":
            networks = service.scan()
            print(json.dumps([item.to_dict() for item in networks], ensure_ascii=False, indent=2))
            return 0

        if args.command == "status":
            status = service.status()
            print(json.dumps(status.to_dict(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "connect":
            password = args.password
            if password is None:
                password = getpass.getpass("Contraseña WiFi (vacío si abierta): ") or None
            status = service.connect(
                args.ssid,
                password,
                persist=not args.no_persist,
            )
            print(json.dumps(status.to_dict(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "disconnect":
            status = service.disconnect()
            print(json.dumps(status.to_dict(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "test":
            ok = service.test_connectivity()
            print(json.dumps({"connectivity_ok": ok}, ensure_ascii=False))
            return 0 if ok else 1

    except WifiError as exc:
        logger.error("%s", exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1

    parser.error("Comando no reconocido")
    return 2


def main() -> None:
    sys.exit(run_wifi_cli())
