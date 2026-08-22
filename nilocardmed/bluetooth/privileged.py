"""Sesiones elevadas para comandos BLE sensibles (contraseña válida 1 h)."""

from __future__ import annotations

import secrets
import threading
import time


PRIVILEGED_COMMANDS: frozenset[str] = frozenset(
    {
        "wifi_connect",
        "wifi_configure",
        "time_sync",
        "cardmed_configure",
        "configure_cardmed",
        "configurar",
        "sampling_set_interval",
        "set_interval",
        "sampling_set_window",
        "set_monitor_window",
        "camera_set_device",
        "set_camera_device",
        "cardmed_scan_qr",
        "scan_cardmed_qr",
    }
)


class PrivilegedSessionStore:
    """Marca tokens autenticados como elevados tras reintroducir contraseña."""

    def __init__(self, privileged_ttl_seconds: int = 3600) -> None:
        self._ttl = privileged_ttl_seconds
        self._elevated_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def elevate(self, token: str) -> None:
        with self._lock:
            self._purge_expired_unlocked()
            self._elevated_until[token] = time.time() + self._ttl

    def is_privileged(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.time()
        with self._lock:
            self._purge_expired_unlocked()
            expires = self._elevated_until.get(token)
            return expires is not None and expires > now

    def clear(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._elevated_until.pop(token, None)

    def _purge_expired_unlocked(self) -> None:
        now = time.time()
        expired = [token for token, expiry in self._elevated_until.items() if expiry <= now]
        for token in expired:
            del self._elevated_until[token]


def passwords_match(provided: str | None, expected: str) -> bool:
    if provided is None:
        return False
    return secrets.compare_digest(provided, expected)
