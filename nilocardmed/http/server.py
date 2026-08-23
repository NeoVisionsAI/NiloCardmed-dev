"""Servidor HTTP liviano para aprovisionamiento local vía WiFi AP."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from nilocardmed import __version__
from nilocardmed.bluetooth.models import CommandRequest
from nilocardmed.bluetooth.protocol import CommandRouter, build_router
from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import BluetoothSettings, EnvironmentSettings, HttpSettings

from nilocardmed.http.static_files import provisioning_index_html, provisioning_mime, read_static

logger = logging.getLogger(__name__)


def _cors_headers(settings: HttpSettings) -> dict[str, str]:
    origin = settings.cors_allow_origin.strip() or "*"
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    }


class ProvisioningHttpHandler(BaseHTTPRequestHandler):
    """Handler HTTP que expone status, config y el mismo protocolo JSON que BLE."""

    router: CommandRouter
    http_settings: HttpSettings
    config_manager: ConfigManager
    bluetooth_settings: BluetoothSettings

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        logger.debug("HTTP %s - %s", self.address_string(), format % args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
            **_cors_headers(self.http_settings),
        }
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, html: str) -> None:
        self._send_bytes(status, html.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_static(self, relative_path: str) -> bool:
        try:
            body = read_static(relative_path)
        except FileNotFoundError:
            return False
        self._send_bytes(200, body, provisioning_mime(relative_path))
        return True

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("Se esperaba un objeto JSON")
        return parsed

    def _extract_token(self, body: dict[str, Any]) -> str | None:
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip() or None
        token = body.get("token")
        return str(token) if token else None

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        for key, value in _cors_headers(self.http_settings).items():
            self.send_header(key, value)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send_bytes(200, provisioning_index_html(), "text/html; charset=utf-8")
            return
        if path.startswith("/assets/"):
            relative = path.lstrip("/")
            if self._serve_static(relative):
                return
            self._send_json(404, {"status": "error", "error": "not_found"})
            return
        if path == "/api/status":
            config = self.config_manager.load()
            self._send_json(
                200,
                {
                    "status": "ok",
                    "device": self.http_settings.device_label,
                    "device_name": config.bluetooth.device_name,
                    "version": __version__,
                },
            )
            return
        if path == "/api/config":
            config = self.config_manager.load()
            self._send_json(
                200,
                {
                    "device_name": config.bluetooth.device_name,
                    "wifi": {
                        "ssid": config.wifi.ssid,
                        "connected": bool(config.wifi.ssid),
                    },
                    "cardmed": config.cardmed.model_dump(mode="json"),
                    "sampling": {
                        "enabled": config.sampling.enabled,
                        "interval_seconds": config.sampling.interval_seconds,
                    },
                    "camera": {
                        "device_path": config.camera.device_path,
                    },
                },
            )
            return
        if path == "/api/dashboard":
            from nilocardmed.system.device_status import build_device_status

            try:
                dashboard = build_device_status(self.router.context)
            except Exception as exc:  # noqa: BLE001 — respuesta JSON al frontend
                logger.exception("Error construyendo /api/dashboard")
                self._send_json(
                    500,
                    {
                        "status": "error",
                        "error": "dashboard_failed",
                        "detail": str(exc),
                    },
                )
                return
            self._send_json(200, dashboard)
            return
        self._send_json(404, {"status": "error", "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/command":
            self._send_json(404, {"status": "error", "error": "not_found"})
            return
        try:
            body = self._read_json_body()
        except (json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"status": "error", "error": str(exc)})
            return

        cmd = body.get("cmd")
        if not cmd:
            self._send_json(400, {"status": "error", "error": "cmd requerido"})
            return

        request = CommandRequest(
            cmd=str(cmd),
            id=body.get("id"),
            token=self._extract_token(body),
            payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
        )
        response = self.router.handle(request)
        self._send_json(200, response.to_dict())


def create_http_server(
    settings: HttpSettings,
    config_manager: ConfigManager,
    env: EnvironmentSettings,
    bluetooth_settings: BluetoothSettings,
) -> ThreadingHTTPServer:
    """Crea el servidor HTTP enlazado al router de comandos existente."""
    router = build_router(bluetooth_settings, config_manager, env)

    class _Handler(ProvisioningHttpHandler):
        pass

    _Handler.router = router
    _Handler.http_settings = settings
    _Handler.config_manager = config_manager
    _Handler.bluetooth_settings = bluetooth_settings

    bind_host = settings.host
    if settings.bind_ap_only and settings.ap_ip:
        bind_host = settings.ap_ip

    bind_attempts = [bind_host]
    if bind_host not in ("0.0.0.0", ""):
        bind_attempts.append(settings.host or "0.0.0.0")

    server: ThreadingHTTPServer | None = None
    last_error: OSError | None = None
    for host in bind_attempts:
        try:
            server = ThreadingHTTPServer((host, settings.port), _Handler)
            if host != bind_host:
                logger.warning(
                    "HTTP enlazado en %s:%s (fallback desde %s)",
                    host,
                    settings.port,
                    bind_host,
                )
            break
        except OSError as exc:
            last_error = exc
            logger.warning("HTTP no pudo enlazar en %s:%s (%s)", host, settings.port, exc)

    if server is None:
        raise last_error or OSError("No se pudo enlazar el servidor HTTP")
    server.allow_reuse_address = True
    server.daemon_threads = True
    return server


class HttpProvisioningService:
    """Hilo daemon con el servidor HTTP de aprovisionamiento."""

    def __init__(
        self,
        settings: HttpSettings,
        config_manager: ConfigManager,
        env: EnvironmentSettings,
        bluetooth_settings: BluetoothSettings,
    ) -> None:
        self.settings = settings
        self._config_manager = config_manager
        self._env = env
        self._bluetooth_settings = bluetooth_settings
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._server is not None

    def start(self, shutdown: threading.Event) -> None:
        if not self.settings.enabled:
            logger.info("HTTP de aprovisionamiento deshabilitado")
            return
        if self._thread and self._thread.is_alive():
            return

        try:
            self._server = create_http_server(
                self.settings,
                self._config_manager,
                self._env,
                self._bluetooth_settings,
            )
        except OSError as exc:
            logger.error("No se pudo iniciar servidor HTTP de aprovisionamiento: %s", exc)
            return

        bind_host = self._server.server_address[0]
        logger.info(
            "Servidor HTTP de aprovisionamiento en http://%s:%s",
            bind_host,
            self.settings.port,
        )

        def _runner() -> None:
            assert self._server is not None
            self._server.timeout = 0.5
            while not shutdown.is_set():
                self._server.handle_request()
            self._server.server_close()

        self._thread = threading.Thread(target=_runner, name="http-provision", daemon=True)
        self._thread.start()

    @property
    def bind_address(self) -> str:
        if self._server is None:
            return "?"
        host, port = self._server.server_address[:2]
        return f"{host}:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
