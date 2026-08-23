"""Consulta de IP pública del dispositivo (para el panel de estado)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_IP_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|[01]?\d?\d)){3}|"
    r"(?:[a-fA-F0-9:]+:+)+[a-fA-F0-9]+)$"
)

_PUBLIC_IP_ENDPOINTS = (
    "https://api.ipify.org",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
)

_CACHE: dict[str, Any] | None = None
_CACHE_AT: float = 0.0
_CACHE_TTL_SECONDS = 60.0


def _normalize_ip(text: str) -> str | None:
    candidate = text.strip().splitlines()[0].strip() if text.strip() else ""
    if candidate and _IP_RE.match(candidate):
        return candidate
    return None


def lookup_public_ip(*, timeout_seconds: float = 4.0, use_cache: bool = True) -> dict[str, Any]:
    """
    Resuelve la IP pública vía servicios HTTP externos.

    Resultado cacheado unos segundos para no ralentizar refrescos del dashboard.
    """
    global _CACHE, _CACHE_AT

    now = time.monotonic()
    if use_cache and _CACHE is not None and now - _CACHE_AT < _CACHE_TTL_SECONDS:
        return dict(_CACHE)

    result: dict[str, Any] = {
        "available": False,
        "ip": None,
        "source": None,
    }

    for url in _PUBLIC_IP_ENDPOINTS:
        try:
            response = httpx.get(
                url,
                timeout=timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": "NiloCardmed/1.0"},
            )
            if response.status_code != 200:
                continue
            ip = _normalize_ip(response.text)
            if ip:
                result = {"available": True, "ip": ip, "source": url}
                break
        except httpx.HTTPError as exc:
            logger.debug("IP pública no disponible desde %s: %s", url, exc)

    if use_cache:
        _CACHE = dict(result)
        _CACHE_AT = now

    return result


def collect_network_addresses(*, private_ip: str | None, wifi_connected: bool) -> dict[str, Any]:
    """Agrega IP privada (WiFi) e IP pública (lookup externo si hay enlace)."""
    payload: dict[str, Any] = {
        "private_ip": private_ip,
        "public_ip": None,
        "public_ip_available": False,
    }
    if not wifi_connected or not private_ip:
        return payload

    public = lookup_public_ip()
    payload["public_ip"] = public.get("ip")
    payload["public_ip_available"] = bool(public.get("available"))
    if public.get("source"):
        payload["public_ip_source"] = public["source"]
    return payload
