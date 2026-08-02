"""Modelos del flujo CardMed."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TestStep:
    """Paso individual de la prueba CardMed (feedback al operador)."""

    name: str
    ok: bool
    message: str | None = None
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"name": self.name, "ok": self.ok}
        if self.message is not None:
            body["message"] = self.message
        if self.data is not None:
            body["data"] = self.data
        return body


@dataclass(slots=True)
class TestResult:
    """Resultado completo de *Probar CardMed*."""

    success: bool
    steps: list[TestStep] = field(default_factory=list)
    capture: dict[str, Any] | None = None
    upload: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "steps": [step.to_dict() for step in self.steps],
            "capture": self.capture,
            "upload": self.upload,
            "error": self.error,
        }


@dataclass(slots=True)
class ConfigureResult:
    """Resultado de configurar CardMed."""

    cardmed: dict[str, Any]
    ser_device_id: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cardmed": self.cardmed,
            "ser_device_id": self.ser_device_id,
            "warnings": self.warnings,
        }
