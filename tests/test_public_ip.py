"""Tests de resolución de IP pública."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nilocardmed.system.public_ip import collect_network_addresses, lookup_public_ip


def test_lookup_public_ip_parses_response():
    with patch("nilocardmed.system.public_ip.httpx.get") as http_get:
        http_get.return_value = MagicMock(status_code=200, text="203.0.113.42\n")
        result = lookup_public_ip(use_cache=False)

    assert result["available"] is True
    assert result["ip"] == "203.0.113.42"
    assert result["source"] == "https://api.ipify.org"


def test_lookup_public_ip_tries_fallback():
    with patch("nilocardmed.system.public_ip.httpx.get") as http_get:
        http_get.side_effect = [
            MagicMock(status_code=503, text=""),
            MagicMock(status_code=200, text="198.51.100.7"),
        ]
        result = lookup_public_ip(use_cache=False)

    assert result["ip"] == "198.51.100.7"
    assert http_get.call_count == 2


def test_collect_network_addresses_skips_public_when_offline():
    result = collect_network_addresses(private_ip=None, wifi_connected=False)
    assert result["private_ip"] is None
    assert result["public_ip"] is None
    assert result["public_ip_available"] is False
