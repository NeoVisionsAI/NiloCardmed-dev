"""Tests de validación CardMed."""

from __future__ import annotations

import pytest

from nilocardmed.cardmed.exceptions import CardMedConfigError
from nilocardmed.cardmed.validation import extract_cardmed_patch, validate_cardmed_patch


def test_extract_flat_patch():
    patch = extract_cardmed_patch({"site_id": "A", "cmd": "ignored"})
    assert patch == {"site_id": "A"}


def test_extract_nested_patch():
    patch = extract_cardmed_patch({"cardmed": {"site_id": "B"}})
    assert patch == {"site_id": "B"}


def test_validate_rejects_empty_site_id():
    with pytest.raises(CardMedConfigError):
        validate_cardmed_patch({"site_id": "  "})


def test_validate_accepts_valid_patch():
    validate_cardmed_patch({"site_id": "SITE-1", "metadata": {"ward": "a"}})
