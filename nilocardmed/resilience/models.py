"""Modelos de salud y resiliencia."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

HealthStatus = Literal["healthy", "degraded", "unhealthy"]


@dataclass(slots=True)
class ComponentHealth:
    """Estado de un subsistema."""

    name: str
    ok: bool
    message: str | None = None
    data: dict[str, Any] | None = None
    severity: HealthStatus = "unhealthy"

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": self.name,
            "ok": self.ok,
            "severity": self.severity,
        }
        if self.message is not None:
            body["message"] = self.message
        if self.data is not None:
            body["data"] = self.data
        return body


@dataclass(slots=True)
class HealthReport:
    """Informe agregado de salud del dispositivo."""

    healthy: bool
    degraded: bool
    status: HealthStatus
    components: list[ComponentHealth] = field(default_factory=list)
    checked_at_epoch: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "degraded": self.degraded,
            "status": self.status,
            "checked_at_epoch": self.checked_at_epoch,
            "components": [component.to_dict() for component in self.components],
        }
