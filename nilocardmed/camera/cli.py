"""CLI de prueba para el módulo de cámara."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from nilocardmed.camera.exceptions import CameraError
from nilocardmed.camera.service import CameraService
from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import EnvironmentSettings
from nilocardmed.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Herramientas de cámara NiloCardmed")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="Listar cámaras detectadas")
    list_parser.add_argument(
        "--include-non-capture",
        action="store_true",
        help="Incluir nodos /dev/video* sin capacidad de captura",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Salida en JSON",
    )

    capture_parser = sub.add_parser("capture-test", help="Capturar imagen de prueba")
    capture_parser.add_argument(
        "--device",
        help="Ruta del dispositivo (/dev/video0). Por defecto, autodetectar",
    )
    capture_parser.add_argument(
        "--output",
        type=Path,
        help="Ruta de salida del JPEG. Por defecto, DATA_DIR/captures/",
    )
    capture_parser.add_argument(
        "--backend",
        choices=["auto", "fswebcam", "ffmpeg"],
        help="Backend de captura a usar",
    )
    capture_parser.add_argument(
        "--json",
        action="store_true",
        help="Salida en JSON",
    )

    return parser


def _device_to_dict(device) -> dict:
    return {
        "id": device.id,
        "path": str(device.path),
        "name": device.name,
        "driver": device.driver,
        "bus_info": device.bus_info,
        "supports_capture": device.supports_capture,
    }


def run_camera_cli(argv: list[str] | None = None) -> int:
    env = EnvironmentSettings()
    setup_logging(
        level=env.log_level,
        structured=env.log_structured,
        log_dir=env.log_dir,
    )

    config = ConfigManager(env).load()
    service = CameraService(config.camera, data_dir=env.data_dir)
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            devices = service.list_cameras(include_non_capture=args.include_non_capture)
            if args.json:
                print(json.dumps([_device_to_dict(item) for item in devices], ensure_ascii=False, indent=2))
            else:
                if not devices:
                    print("No se detectaron cámaras.")
                for device in devices:
                    label = device.name or device.id
                    print(f"{device.path}\t{label}\tcapture={device.supports_capture}")
            return 0

        if args.command == "capture-test":
            result = service.capture(
                device_path=args.device,
                output_path=args.output,
                backend=args.backend,
            )
            if args.json:
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(f"Captura OK: {result.output_path} ({result.size_bytes} bytes)")
            return 0

    except CameraError as exc:
        logger.error("%s", exc)
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error("Comando no reconocido")
    return 2


def main() -> None:
    sys.exit(run_camera_cli())
