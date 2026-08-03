"""CLI de salud y resiliencia."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import EnvironmentSettings
from nilocardmed.logging_setup import setup_logging
from nilocardmed.resilience.health import HealthService

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Salud y resiliencia NiloCardmed")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="Informe de salud JSON")
    status.add_argument("--json", action="store_true")
    check = sub.add_parser("check", help="Comprobar salud (exit code 0/1)")
    check.add_argument("--exit-code", action="store_true", help="Salir 1 si no healthy")
    check.add_argument("--json", action="store_true")
    return parser


def run_health_cli(argv: list[str] | None = None) -> int:
    env = EnvironmentSettings()
    setup_logging(
        level=env.log_level,
        structured=env.log_structured,
        log_dir=env.log_dir,
    )

    config_manager = ConfigManager(env)
    config = config_manager.load()
    service = HealthService(config, env)

    parser = _build_parser()
    args = parser.parse_args(argv)

    report = service.summary_dict()
    status = report.get("status", "unhealthy")

    if args.command == "status":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.command == "check":
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            for component in report.get("components", []):
                mark = "OK" if component.get("ok") else component.get("severity", "FAIL").upper()
                print(f"{component.get('name')}: {mark} — {component.get('message', '')}")
            print("---")
            print("Estado:", status.upper())
        if args.exit_code and status == "unhealthy":
            return 1
        return 0

    parser.error("Comando no reconocido")
    return 2


def main() -> None:
    sys.exit(run_health_cli())
