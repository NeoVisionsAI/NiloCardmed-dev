"""CLI del módulo CardMed."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from nilocardmed.cardmed.exceptions import CardMedConfigError, CardMedError
from nilocardmed.cardmed.service import CardMedService
from nilocardmed.cardmed.validation import extract_cardmed_patch
from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import EnvironmentSettings
from nilocardmed.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CardMed — configuración y prueba")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("get", help="Mostrar configuración CardMed actual")

    configure = sub.add_parser("configure", help="Aplicar configuración CardMed")
    configure.add_argument("--json", help="Patch JSON (campos CardMed)")
    configure.add_argument("--site-id")
    configure.add_argument("--device-label")
    configure.add_argument("--location")
    configure.add_argument("--operator-id")
    configure.add_argument("--enabled", choices=("true", "false"))
    configure.add_argument("--output-json", action="store_true", dest="output_json")

    test = sub.add_parser("test", help="Ejecutar prueba CardMed end-to-end")
    test.add_argument("--device", help="Ruta V4L2 opcional")
    test.add_argument("--dry-run", action="store_true", help="Captura y valida sin enviar a SER")
    test.add_argument("--skip-upload", action="store_true")
    test.add_argument("--json", action="store_true")

    return parser


def _configure_patch_from_args(args) -> dict:
    if args.json:
        parsed = json.loads(args.json)
        if not isinstance(parsed, dict):
            raise CardMedConfigError("--json debe ser un objeto")
        return extract_cardmed_patch(parsed)

    patch: dict = {}
    if args.site_id is not None:
        patch["site_id"] = args.site_id
    if args.device_label is not None:
        patch["device_label"] = args.device_label
    if args.location is not None:
        patch["location"] = args.location
    if args.operator_id is not None:
        patch["operator_id"] = args.operator_id
    if args.enabled is not None:
        patch["enabled"] = args.enabled == "true"
    return patch


def run_cardmed_cli(argv: list[str] | None = None) -> int:
    env = EnvironmentSettings()
    setup_logging(
        level=env.log_level,
        structured=env.log_structured,
        log_dir=env.log_dir,
    )

    config_manager = ConfigManager(env)
    config_manager.load()
    service = CardMedService(config_manager, env)

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "get":
            payload = service.get_config()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.command == "configure":
            patch = _configure_patch_from_args(args)
            result = service.configure(patch)
            if args.output_json:
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                print("CardMed configurado:")
                print(json.dumps(result.cardmed, ensure_ascii=False, indent=2))
                if result.warnings:
                    print("Avisos:", ", ".join(result.warnings))
            return 0

        if args.command == "test":
            result = service.run_test(
                device_path=args.device,
                dry_run=args.dry_run or None,
                skip_upload=args.skip_upload or None,
            )
            if args.json:
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                for step in result.steps:
                    status = "OK" if step.ok else "FAIL"
                    print(f"{step.name}: {status} — {step.message or ''}")
                print("---")
                print("Prueba:", "OK" if result.success else "FAIL")
            return 0 if result.success else 1

    except CardMedError as exc:
        logger.error("%s", exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1

    parser.error("Comando no reconocido")
    return 2


def main() -> None:
    sys.exit(run_cardmed_cli())
