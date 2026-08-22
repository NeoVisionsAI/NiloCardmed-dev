"""Tests de códigos y QR CardMed."""

from __future__ import annotations

import pytest

from nilocardmed.cardmed.config_codes import parse_config_code, patch_from_qr_payload
from nilocardmed.cardmed.exceptions import CardMedConfigError


def test_parse_config_code_pipe_format():
    patch = parse_config_code("SITE-001|Sala 3|op-42|Planta baja")
    assert patch["site_id"] == "SITE-001"
    assert patch["device_label"] == "Sala 3"
    assert patch["operator_id"] == "op-42"
    assert patch["location"] == "Planta baja"
    assert patch["enabled"] is True


def test_parse_config_code_json_cardmed_wrapper():
    patch = parse_config_code('{"cardmed": {"site_id": "ABC", "enabled": true}}')
    assert patch["site_id"] == "ABC"
    assert patch["enabled"] is True


def test_patch_from_qr_payload():
    patch = patch_from_qr_payload('{"site_id":"QR-1","device_label":"Cam 1"}')
    assert patch["site_id"] == "QR-1"


def test_parse_config_code_empty_raises():
    with pytest.raises(CardMedConfigError):
        parse_config_code("   ")
