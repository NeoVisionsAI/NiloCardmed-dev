"""CLI de prueba del cliente SER."""

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
from nilocardmed.ser_client.client import SerClient
from nilocardmed.ser_client.exceptions import SerClientError
from nilocardmed.ser_client.models import SamplePayload

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Herramientas cliente SER")
    sub = parser.add_subparsers(dest="command", required=True)

    send = sub.add_parser("send-test", help="Enviar imagen de prueba a SER")
    send.add_argument(
        "--image",
        type=Path,
        help="JPEG a enviar. Si se omite, captura una imagen nueva",
    )
    send.add_argument(
        "--device",
        help="Dispositivo de cámara si hay que capturar (--image no indicado)",
    )
    send.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostrar la petición que se enviaría sin ejecutarla",
    )
    send.add_argument(
        "--json",
        action="store_true",
        help="Salida en JSON",
    )

    return parser


def _load_image_bytes(
    *,
    image_path: Path | None,
    device: str | None,
    config_manager: ConfigManager,
    env: EnvironmentSettings,
) -> tuple[bytes, str]:
    if image_path is not None:
        if not image_path.is_file():
            raise SerClientError(f"Imagen no encontrada: {image_path}")
        return image_path.read_bytes(), image_path.name

    camera_service = CameraService(config_manager.get().camera, data_dir=env.data_dir)
    capture = camera_service.capture(device_path=device)
    return capture.output_path.read_bytes(), capture.output_path.name


def run_ser_cli(argv: list[str] | None = None) -> int:
    env = EnvironmentSettings()
    setup_logging(
        level=env.log_level,
        structured=env.log_structured,
        log_dir=env.log_dir,
    )

    config_manager = ConfigManager(env)
    config = config_manager.load()
    client = SerClient(config.ser)

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        image_bytes, filename = _load_image_bytes(
            image_path=args.image,
            device=args.device,
            config_manager=config_manager,
            env=env,
        )
        payload = SamplePayload(
            image_bytes=image_bytes,
            filename=filename,
            device_id=config.ser.device_id,
        )

        if args.dry_run:
            summary = client.dry_run(payload)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        result = client.upload_sample(payload)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(
                f"Envío OK: HTTP {result.status_code} "
                f"({result.elapsed_ms:.0f} ms, {result.attempts} intento(s))"
            )
            if result.sample_ref:
                print(f"sample_ref={result.sample_ref}")
        return 0

    except (SerClientError, CameraError) as exc:
        logger.error("%s", exc)
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    sys.exit(run_ser_cli())
