"""Protocolo JSON extensible sobre GATT."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nilocardmed.bluetooth.auth import AuthSessionStore
from nilocardmed.bluetooth.capture_cache import CaptureCache
from nilocardmed.bluetooth.command_errors import BluetoothCommandError
from nilocardmed.bluetooth.exceptions import BluetoothAuthError, BluetoothProtocolError
from nilocardmed.bluetooth.models import CommandRequest, CommandResponse
from nilocardmed.bluetooth.privileged import PRIVILEGED_COMMANDS, PrivilegedSessionStore, passwords_match
from nilocardmed.config.manager import ConfigManager
from nilocardmed.config.models import BluetoothSettings, EnvironmentSettings

logger = logging.getLogger(__name__)

CommandHandler = Callable[["CommandContext", CommandRequest], dict[str, Any]]


@dataclass(slots=True)
class CommandContext:
    """Contexto compartido entre handlers de comandos."""

    settings: BluetoothSettings
    config_manager: ConfigManager
    env: EnvironmentSettings
    sessions: AuthSessionStore
    capture_cache: CaptureCache
    privileged: PrivilegedSessionStore


class CommandRouter:
    """Enruta comandos JSON a handlers registrados."""

    def __init__(self, context: CommandContext) -> None:
        self._context = context
        self._handlers: dict[str, CommandHandler] = {}

    @property
    def context(self) -> CommandContext:
        return self._context

    def register(self, command: str, handler: CommandHandler) -> None:
        self._handlers[command] = handler

    def handle_raw(self, raw: bytes | str) -> bytes:
        if isinstance(raw, bytes):
            if len(raw) > self._context.settings.max_message_bytes:
                raise BluetoothProtocolError("Mensaje demasiado grande")
            text = raw.decode("utf-8").strip()
        else:
            text = raw.strip()

        if not text:
            raise BluetoothProtocolError("Mensaje vacío")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BluetoothProtocolError("JSON inválido") from exc

        if not isinstance(data, dict):
            raise BluetoothProtocolError("Se esperaba un objeto JSON")

        request = CommandRequest.from_dict(data)
        response = self.handle(request)
        encoded = json.dumps(response.to_dict(), ensure_ascii=False).encode("utf-8")
        max_bytes = self._max_response_bytes_for(request.cmd)
        if len(encoded) > max_bytes:
            fallback = CommandResponse(
                ok=False,
                cmd=request.cmd or "unknown",
                id=request.id,
                error="response_too_large",
            )
            encoded = json.dumps(fallback.to_dict(), ensure_ascii=False).encode("utf-8")
        return encoded

    def _max_response_bytes_for(self, command: str) -> int:
        if command in {"camera_capture_test", "capture_test"}:
            return self._context.settings.max_image_response_bytes
        if command == "camera_capture_chunk":
            return self._context.settings.max_chunk_response_bytes
        return self._context.settings.max_response_bytes

    def handle(self, request: CommandRequest) -> CommandResponse:
        if not request.cmd:
            return CommandResponse(ok=False, cmd="", id=request.id, error="cmd requerido")

        settings = self._context.settings
        if (
            settings.require_auth
            and request.cmd not in settings.allowed_commands_without_auth
            and not self._context.sessions.validate(request.token)
        ):
            return CommandResponse(
                ok=False,
                cmd=request.cmd,
                id=request.id,
                error="unauthorized",
            )

        if request.cmd in PRIVILEGED_COMMANDS:
            token = request.token
            if not self._context.privileged.is_privileged(token):
                password = request.payload.get("password")
                expected = settings.password.get_secret_value()
                if passwords_match(str(password) if password is not None else None, expected):
                    if token:
                        self._context.privileged.elevate(token)
                else:
                    return CommandResponse(
                        ok=False,
                        cmd=request.cmd,
                        id=request.id,
                        error="privileged_auth_required",
                    )

        handler = self._handlers.get(request.cmd)
        if handler is None:
            return CommandResponse(
                ok=False,
                cmd=request.cmd,
                id=request.id,
                error=f"comando no soportado: {request.cmd}",
            )

        try:
            data = handler(self._context, request)
            return CommandResponse(ok=True, cmd=request.cmd, id=request.id, data=data)
        except BluetoothCommandError as exc:
            return CommandResponse(
                ok=False,
                cmd=request.cmd,
                id=request.id,
                error=exc.as_error_string(),
            )
        except BluetoothAuthError as exc:
            return CommandResponse(ok=False, cmd=request.cmd, id=request.id, error=str(exc))
        except Exception as exc:
            logger.exception("Error ejecutando comando %s", request.cmd)
            return CommandResponse(
                ok=False,
                cmd=request.cmd,
                id=request.id,
                error=f"error interno: {exc}",
            )


def build_router(
    settings: BluetoothSettings,
    config_manager: ConfigManager,
    env: EnvironmentSettings,
) -> CommandRouter:
    from nilocardmed.bluetooth.handlers import register_operation_handlers

    sessions = AuthSessionStore(token_ttl_seconds=settings.token_ttl_seconds)
    privileged = PrivilegedSessionStore(privileged_ttl_seconds=settings.privileged_ttl_seconds)
    context = CommandContext(
        settings=settings,
        config_manager=config_manager,
        env=env,
        sessions=sessions,
        capture_cache=CaptureCache(),
        privileged=privileged,
    )
    router = CommandRouter(context)
    register_operation_handlers(router)
    return router
