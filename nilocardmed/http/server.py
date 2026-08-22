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

logger = logging.getLogger(__name__)

_CONFIG_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NiloCardmed — Configuración</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 1.5rem; max-width: 640px; }
    h1 { font-size: 1.25rem; }
    .ok { color: #0a0; }
    .err { color: #c00; }
    pre { background: #f4f4f4; padding: 0.75rem; overflow: auto; }
    label { display: block; margin-top: 0.75rem; }
    input, button { font-size: 1rem; padding: 0.4rem; }
    button { margin-top: 1rem; }
  </style>
</head>
<body>
  <h1>NiloCardmed — Configuración local</h1>
  <p>Conectado al punto de acceso del dispositivo. Usa la API JSON o el formulario.</p>
  <p id="status">Comprobando…</p>
  <h2>Estado</h2>
  <pre id="config">—</pre>
  <h2>Autenticación</h2>
  <label>Contraseña <input type="password" id="password"></label>
  <button type="button" id="auth-btn">Obtener token</button>
  <h2>WiFi (STA)</h2>
  <label>SSID <input id="ssid"></label>
  <label>Clave WiFi <input type="password" id="wifi-pass"></label>
  <button type="button" id="wifi-btn">Conectar WiFi</button>
  <pre id="result"></pre>
  <script>
    const result = document.getElementById('result');
    let token = sessionStorage.getItem('nilocardmed_token') || '';

    async function api(path, opts = {}) {
      const headers = Object.assign({'Content-Type': 'application/json'}, opts.headers || {});
      if (token) headers['Authorization'] = 'Bearer ' + token;
      const res = await fetch(path, Object.assign({headers}, opts));
      const text = await res.text();
      try { return {ok: res.ok, data: JSON.parse(text)}; }
      catch { return {ok: res.ok, data: text}; }
    }

    async function refresh() {
      const st = await api('/api/status');
      document.getElementById('status').textContent = st.data.status === 'ok'
        ? 'Dispositivo alcanzable (' + st.data.device + ')' : 'Error';
      document.getElementById('status').className = st.data.status === 'ok' ? 'ok' : 'err';
      const cfg = await api('/api/config');
      document.getElementById('config').textContent = JSON.stringify(cfg.data, null, 2);
    }

    document.getElementById('auth-btn').onclick = async () => {
      const password = document.getElementById('password').value;
      const r = await api('/api/command', {
        method: 'POST',
        body: JSON.stringify({cmd: 'auth', payload: {password}})
      });
      if (r.data.ok && r.data.data && r.data.data.token) {
        token = r.data.data.token;
        sessionStorage.setItem('nilocardmed_token', token);
      }
      result.textContent = JSON.stringify(r.data, null, 2);
    };

    document.getElementById('wifi-btn').onclick = async () => {
      const ssid = document.getElementById('ssid').value;
      const password = document.getElementById('wifi-pass').value;
      const r = await api('/api/command', {
        method: 'POST',
        body: JSON.stringify({cmd: 'wifi_connect', payload: {ssid, password}})
      });
      result.textContent = JSON.stringify(r.data, null, 2);
      refresh();
    };

    refresh();
  </script>
</body>
</html>
"""


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

    def _send_html(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
            self._send_html(200, _CONFIG_HTML)
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
                },
            )
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

    bind_host = settings.ap_ip if settings.bind_ap_only else settings.host
    try:
        server = ThreadingHTTPServer((bind_host, settings.port), _Handler)
    except OSError as exc:
        if settings.bind_ap_only and bind_host not in ("0.0.0.0", ""):
            logger.warning(
                "HTTP no pudo enlazar en %s:%s (%s); usando %s",
                bind_host,
                settings.port,
                exc,
                settings.host,
            )
            server = ThreadingHTTPServer((settings.host, settings.port), _Handler)
        else:
            raise
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

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
