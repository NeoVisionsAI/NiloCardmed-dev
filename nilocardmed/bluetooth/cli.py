"""CLI del servicio Bluetooth."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from nilocardmed.bluetooth.exceptions import BluetoothError
from nilocardmed.bluetooth.framing import BleFramer, decode_frames
from nilocardmed.bluetooth.service import BluetoothService
from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import AppConfig, EnvironmentSettings
from nilocardmed.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Herramientas Bluetooth NiloCardmed")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Ejecutar servidor GATT BLE en primer plano")

    test = sub.add_parser(
        "test-session",
        help="Probar auth + ping con backend mock (sin hardware BLE)",
    )
    test.add_argument("--password", help="Contraseña de prueba")
    test.add_argument("--json", action="store_true")

    suite = sub.add_parser(
        "test-commands",
        help="Probar suite Fase 7 (mock BLE + WiFi mock)",
    )
    suite.add_argument("--password", help="Contraseña de prueba")
    suite.add_argument(
        "--skip-camera-capture",
        action="store_true",
        help="Omitir camera_capture_test y chunks",
    )
    suite.add_argument("--json", action="store_true")

    framing = sub.add_parser(
        "test-framing",
        help="Verificar fragmentación BLE (Web Bluetooth / MTU)",
    )
    framing.add_argument("--password", help="Contraseña de prueba")
    framing.add_argument("--json", action="store_true")

    info = sub.add_parser("info", help="Mostrar UUIDs y configuración BLE")
    info.add_argument("--json", action="store_true")

    return parser


def _mock_test_config(config: AppConfig) -> AppConfig:
    return config.model_copy(
        update={
            "bluetooth": config.bluetooth.model_copy(update={"backend": "mock"}),
            "wifi": config.wifi.model_copy(update={"backend": "mock"}),
        }
    )


def _send(service: BluetoothService, payload: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(payload, ensure_ascii=False).encode()
    frames = service.process_mock_frames(raw)
    if not frames:
        raise BluetoothError("Respuesta BLE incompleta (esperando más frames RX)")
    response_bytes = decode_frames(frames)
    return json.loads(response_bytes.decode())


def _step_success(name: str, resp: dict[str, Any]) -> bool:
    if not resp.get("ok"):
        return False
    if name == "cardmed_test":
        return bool(resp.get("data", {}).get("success"))
    return True


def _run_phase7_suite(
    service: BluetoothService,
    *,
    password: str,
    skip_camera_capture: bool,
) -> dict[str, Any]:
    results: dict[str, Any] = {}

    auth_resp = _send(service, {"cmd": "auth", "password": password, "id": "auth"})
    results["auth"] = auth_resp
    if not auth_resp.get("ok"):
        return {"ok": False, "results": results}

    token = auth_resp["data"]["token"]
    base = {"token": token}

    steps: list[tuple[str, dict[str, Any]]] = [
        ("ping", {**base, "cmd": "ping", "id": "ping"}),
        ("commands_list", {**base, "cmd": "commands_list", "id": "commands"}),
        ("camera_list", {**base, "cmd": "camera_list", "id": "cameras"}),
        ("sampling_get", {**base, "cmd": "sampling_get", "id": "sampling-get"}),
        (
            "sampling_set_interval",
            {
                **base,
                "cmd": "sampling_set_interval",
                "interval_seconds": 120,
                "id": "sampling-interval",
            },
        ),
        (
            "sampling_set_window",
            {
                **base,
                "cmd": "sampling_set_window",
                "monitor_start": -1,
                "monitor_end": -1,
                "id": "sampling-window",
            },
        ),
        ("wifi_scan", {**base, "cmd": "wifi_scan", "id": "wifi-scan"}),
        (
            "wifi_connect",
            {
                **base,
                "cmd": "wifi_connect",
                "ssid": "NiloCardmed-Lab",
                "password": "test",
                "persist": True,
                "id": "wifi-connect",
            },
        ),
        ("wifi_status", {**base, "cmd": "wifi_status", "id": "wifi-status"}),
        ("wifi_test", {**base, "cmd": "wifi_test", "id": "wifi-test"}),
        (
            "cardmed_configure",
            {
                **base,
                "cmd": "cardmed_configure",
                "site_id": "TEST-SITE",
                "device_label": "NiloCardmed-Lab",
                "operator_id": "test-operator",
                "location": "Laboratorio",
                "id": "cardmed-configure",
            },
        ),
        ("cardmed_get", {**base, "cmd": "cardmed_get", "id": "cardmed-get"}),
    ]

    if not skip_camera_capture:
        steps.append(
            (
                "cardmed_test",
                {
                    **base,
                    "cmd": "cardmed_test",
                    "skip_upload": True,
                    "id": "cardmed-test",
                },
            )
        )

    if not skip_camera_capture:
        steps.extend(
            [
                (
                    "camera_capture_test",
                    {
                        **base,
                        "cmd": "camera_capture_test",
                        "mode": "chunked",
                        "id": "capture",
                    },
                ),
                (
                    "camera_capture_chunk",
                    {
                        **base,
                        "cmd": "camera_capture_chunk",
                        "index": 0,
                        "id": "capture-chunk",
                    },
                ),
            ]
        )

    all_ok = True
    capture_id: str | None = None

    for name, payload in steps:
        if name == "camera_capture_chunk" and capture_id:
            payload = {**payload, "capture_id": capture_id}

        resp = _send(service, payload)
        results[name] = resp

        if name == "camera_capture_test" and resp.get("ok"):
            capture_id = resp.get("data", {}).get("capture_id")

        step_ok = _step_success(name, resp)

        if not step_ok:
            if name in {"camera_capture_test", "camera_capture_chunk"}:
                results[name]["note"] = (
                    "Sin cámara USB o fallo de captura; usa --skip-camera-capture en desarrollo"
                )
            elif name == "cardmed_test":
                results[name]["note"] = (
                    "Prueba CardMed fallida; revisa data.steps o conecta cámara USB"
                )
            all_ok = False

    return {"ok": all_ok, "results": results}


def _run_framing_test(service: BluetoothService, *, password: str) -> dict[str, Any]:
    """Valida encode/decode de frames y un comando real multi-frame."""
    framer = BleFramer(
        enabled=True,
        max_notification_bytes=160,
        frame_payload_bytes=60,
    )
    sample = json.dumps(
        {"ok": True, "cmd": "framing_self_test", "data": {"payload": "x" * 400}},
        ensure_ascii=False,
    ).encode()
    encoded = framer.encode_frames(sample)
    decoded = json.loads(decode_frames(encoded).decode())

    auth = _send(service, {"cmd": "auth", "password": password, "id": "auth"})
    token = auth["data"]["token"]
    raw = json.dumps({"cmd": "commands_list", "token": token, "id": "cmds"}, ensure_ascii=False).encode()
    live_frames = service.process_mock_frames(raw)
    live_decoded = json.loads(decode_frames(live_frames).decode())

    return {
        "ok": (
            decoded.get("ok")
            and auth.get("ok")
            and live_decoded.get("ok")
            and len(encoded) > 1
            and len(live_frames) > 1
        ),
        "self_test_frames": len(encoded),
        "live_command_frames": len(live_frames),
        "commands_count": len(live_decoded.get("data", {}).get("commands", [])),
    }


def _setup_mock_service(
    env: EnvironmentSettings,
    *,
    isolated: bool,
    bluetooth_overrides: dict[str, Any] | None = None,
) -> tuple[ConfigManager, BluetoothService, AppConfig]:
    if isolated:
        temp_root = Path(tempfile.mkdtemp(prefix="nilocardmed-bt-test-"))
        env = env.model_copy(update={"data_dir": temp_root})

    config_manager = ConfigManager(env)
    config = _mock_test_config(config_manager.load())
    if bluetooth_overrides:
        config = config.model_copy(
            update={
                "bluetooth": config.bluetooth.model_copy(update=bluetooth_overrides),
            }
        )
    config_manager._config = config
    service = BluetoothService(config.bluetooth, config_manager, env)
    return config_manager, service, config


def run_bluetooth_cli(argv: list[str] | None = None) -> int:
    env = EnvironmentSettings()
    setup_logging(
        level=env.log_level,
        structured=env.log_structured,
        log_dir=env.log_dir,
    )

    config_manager = ConfigManager(env)
    config = config_manager.load()

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "info":
            payload = {
                "enabled": config.bluetooth.enabled,
                "backend": config.bluetooth.backend,
                "device_name": config.bluetooth.device_name,
                "service_uuid": config.bluetooth.service_uuid,
                "rx_characteristic_uuid": config.bluetooth.rx_characteristic_uuid,
                "tx_characteristic_uuid": config.bluetooth.tx_characteristic_uuid,
                "require_auth": config.bluetooth.require_auth,
                "token_ttl_seconds": config.bluetooth.token_ttl_seconds,
                "capture_test_mode": config.bluetooth.capture_test_mode,
                "capture_chunk_size": config.bluetooth.capture_chunk_size,
                "ble_framing_enabled": config.bluetooth.ble_framing_enabled,
                "ble_max_notification_bytes": config.bluetooth.ble_max_notification_bytes,
                "ble_frame_payload_bytes": config.bluetooth.ble_frame_payload_bytes,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.command in {"test-session", "test-commands", "test-framing"}:
            framing_overrides = (
                {"ble_max_notification_bytes": 160, "ble_frame_payload_bytes": 60}
                if args.command == "test-framing"
                else None
            )
            _, service, test_config = _setup_mock_service(
                env,
                isolated=args.command in {"test-commands", "test-framing"},
                bluetooth_overrides=framing_overrides,
            )
            password = args.password or test_config.bluetooth.password.get_secret_value()

            if args.command == "test-framing":
                result = _run_framing_test(service, password=password)
                if args.json:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(f"self_test_frames: {result['self_test_frames']}")
                    print(f"live_command_frames: {result['live_command_frames']}")
                    print(f"commands_count: {result['commands_count']}")
                    print("---")
                    print("Framing:", "OK" if result["ok"] else "FAIL")
                return 0 if result["ok"] else 1

            if args.command == "test-session":
                auth_resp = _send(service, {"cmd": "auth", "password": password, "id": "1"})
                if not auth_resp.get("ok"):
                    raise BluetoothError(auth_resp.get("error", "auth failed"))

                token = auth_resp["data"]["token"]
                ping_resp = _send(service, {"cmd": "ping", "token": token, "id": "2"})
                result = {"auth": auth_resp, "ping": ping_resp}
                if args.json:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print("auth OK, ping OK:", ping_resp.get("data"))
                return 0 if ping_resp.get("ok") else 1

            summary = _run_phase7_suite(
                service,
                password=password,
                skip_camera_capture=args.skip_camera_capture,
            )
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                for name, resp in summary["results"].items():
                    status = "OK" if _step_success(name, resp) else "FAIL"
                    print(f"{name}: {status}")
                    if not resp.get("ok"):
                        print(f"  error: {resp.get('error')}")
                print("---")
                print("Suite:", "OK" if summary["ok"] else "FAIL")
            return 0 if summary["ok"] else 1

        if args.command == "run":
            bt_settings = config.bluetooth
            bt_service = BluetoothService(bt_settings, config_manager, env)
            shutdown = threading.Event()

            def _handle_signal(_signum, _frame) -> None:
                shutdown.set()

            import signal

            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)

            bt_service.start(shutdown)
            logger.info(
                "Servidor BLE en ejecución (backend=%s). Pulsa Ctrl+C para salir.",
                bt_service.backend.name,
            )
            while not shutdown.wait(timeout=1):
                pass
            bt_service.stop(shutdown)
            return 0

    except BluetoothError as exc:
        logger.error("%s", exc)
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1

    parser.error("Comando no reconocido")
    return 2


def main() -> None:
    sys.exit(run_bluetooth_cli())
