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


def handle_cardmed_scan_qr(ctx: CommandContext, request: CommandRequest) -> dict[str, Any]:
    from nilocardmed.camera.exceptions import CameraError
    from nilocardmed.camera.service import CameraService
    from nilocardmed.cardmed.config_codes import decode_qr_from_image, patch_from_qr_payload

    config = ctx.config_manager.get()
    device = request.payload.get("device") or config.camera.device_path
    auto_apply = bool(request.payload.get("apply", True))

    camera_service = CameraService(config.camera, data_dir=ctx.env.data_dir)
    try:
        capture = camera_service.capture(device_path=device)
    except CameraError as exc:
        raise BluetoothCommandError("camera_error", str(exc)) from exc

    try:
        qr_payload = decode_qr_from_image(capture.output_path)
        patch = patch_from_qr_payload(qr_payload)
    except CardMedConfigError as exc:
        raise BluetoothCommandError("cardmed_config_error", str(exc)) from exc

    response: dict[str, Any] = {
        "qr_payload": qr_payload,
        "patch": patch,
        "capture": {
            "device_path": str(capture.device_path),
            "output_path": str(capture.output_path),
            "size_bytes": capture.size_bytes,
        },
    }

    if auto_apply:
        configure_request = CommandRequest(cmd="cardmed_configure", token=request.token, payload=patch)
        configured = handle_cardmed_configure(ctx, configure_request)
        response["configured"] = configured

    return response
