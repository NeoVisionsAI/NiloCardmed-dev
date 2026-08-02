"""CLI del motor de muestreo."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import EnvironmentSettings
from nilocardmed.logging_setup import setup_logging
from nilocardmed.sampler.engine import SamplerEngine
from nilocardmed.sampler.window import evaluate_window

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Herramientas de muestreo NiloCardmed")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Estado de la ventana y configuración de muestreo")
    sub.add_parser("run-once", help="Ejecutar un ciclo captura + envío")

    run = sub.add_parser("run", help="Ejecutar bucle de muestreo en primer plano")
    run.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Detener tras N ciclos (0 = infinito hasta ventana/shutdown)",
    )

    return parser


def run_sampler_cli(argv: list[str] | None = None) -> int:
    env = EnvironmentSettings()
    setup_logging(
        level=env.log_level,
        structured=env.log_structured,
        log_dir=env.log_dir,
    )

    config_manager = ConfigManager(env)
    config = config_manager.load()
    engine = SamplerEngine(config_manager, env)
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        window = evaluate_window(config.sampling)
        payload = {
            "sampling": config.sampling.model_dump(),
            "window": window.to_dict(),
            "engine_state": engine.state.to_dict(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "run-once":
        result = engine.run_once(config)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.success else 1

    if args.command == "run":
        import threading

        shutdown = threading.Event()

        def _runner() -> None:
            try:
                engine.run(shutdown)
            except Exception:
                logger.exception("Error fatal en muestreo")
                shutdown.set()

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()

        cycles = 0
        while not shutdown.is_set():
            state = engine.state
            if args.max_cycles and state.cycles_total >= args.max_cycles:
                logger.info("max-cycles alcanzado (%s)", args.max_cycles)
                shutdown.set()
                break
            if not state.running and state.stop_reason:
                break
            time.sleep(0.5)

        shutdown.set()
        thread.join(timeout=30)
        return 0 if engine.state.stop_reason != "max_consecutive_failures" else 1

    parser.error("Comando no reconocido")
    return 2


def main() -> None:
    sys.exit(run_sampler_cli())
