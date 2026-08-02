"""Modelos del protocolo Bluetooth."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CommandRequest:
    """Petición JSON recibida por BLE."""

    cmd: str
    id: str | None = None
    token: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandRequest:
        cmd = str(data.get("cmd", "")).strip()
        request_id = data.get("id")
        token = data.get("token")
        reserved = {"cmd", "id", "token", "password"}
        payload = {key: value for key, value in data.items() if key not in reserved}
        if "password" in data:
            payload["password"] = data["password"]
        return cls(cmd=cmd, id=str(request_id) if request_id is not None else None, token=token, payload=payload)


@dataclass(slots=True)
class CommandResponse:
    """Respuesta JSON enviada por BLE."""

    ok: bool
    cmd: str
    id: str | None = None
    data: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"ok": self.ok, "cmd": self.cmd}
        if self.id is not None:
            body["id"] = self.id
        if self.data is not None:
            body["data"] = self.data
        if self.error is not None:
            body["error"] = self.error
        return body
