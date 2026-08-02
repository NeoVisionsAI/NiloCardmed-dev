"""Gestión de sesiones autenticadas vía token."""

from __future__ import annotations

import secrets
import threading
import time


class AuthSessionStore:
    """Almacén thread-safe de tokens emitidos tras autenticación."""

    def __init__(self, token_ttl_seconds: int = 3600) -> None:
        self._token_ttl_seconds = token_ttl_seconds
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue_token(self) -> tuple[str, int]:
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + self._token_ttl_seconds
        with self._lock:
            self._purge_expired_unlocked()
            self._tokens[token] = expires_at
        return token, self._token_ttl_seconds

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.time()
        with self._lock:
            self._purge_expired_unlocked()
            expires_at = self._tokens.get(token)
            if expires_at is None:
                return False
            if expires_at <= now:
                del self._tokens[token]
                return False
            return True

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._tokens.pop(token, None)

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()

    def _purge_expired_unlocked(self) -> None:
        now = time.time()
        expired = [token for token, expiry in self._tokens.items() if expiry <= now]
        for token in expired:
            del self._tokens[token]
