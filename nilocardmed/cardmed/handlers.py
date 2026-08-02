"""Handlers Bluetooth para CardMed (Fase 8)."""

from __future__ import annotations

from typing import Any

from nilocardmed.bluetooth.command_errors import BluetoothCommandError
from nilocardmed.bluetooth.models import CommandRequest
from nilocardmed.bluetooth.protocol import CommandContext
from nilocardmed.cardmed.exceptions import CardMedConfigError
from nilocardmed.cardmed.service import CardMedService


def _service(ctx: CommandContext) -> CardMedService:
    return CardMedService(ctx.config_manager, ctx.env)


def handle_cardmed_get(ctx: CommandContext, _request: CommandRequest) -> dict[str, Any]:
    return _service(ctx).get_config()


def handle_cardmed_configure(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    try:
        result = _service(ctx).configure(request.payload)
    except CardMedConfigError as exc:
        raise BluetoothCommandError("cardmed_config_error", str(exc)) from exc
    return result.to_dict()


def handle_cardmed_test(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    device = request.payload.get("device")
    dry_run = request.payload.get("dry_run")
    skip_upload = request.payload.get("skip_upload")

    result = _service(ctx).run_test(
        device_path=str(device) if device else None,
        dry_run=dry_run if dry_run is not None else None,
        skip_upload=bool(skip_upload) if skip_upload is not None else None,
    )
    return result.to_dict()
