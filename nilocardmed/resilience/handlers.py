"""Handlers Bluetooth de salud (Fase 9)."""

from __future__ import annotations

from typing import Any

from nilocardmed.bluetooth.models import CommandRequest
from nilocardmed.bluetooth.protocol import CommandContext
from nilocardmed.resilience.health import HealthService


def handle_health_status(ctx: CommandContext, _request: CommandRequest) -> dict[str, Any]:
    config = ctx.config_manager.get()
    return HealthService(config, ctx.env).summary_dict()
