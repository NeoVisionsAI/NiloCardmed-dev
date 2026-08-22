"""Configuración del anuncio LE (LocalName para Web Bluetooth)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from nilocardmed.bluetooth.backends import BluezBluetoothBackend, MAX_LE_AD_LOCAL_NAME_BYTES


def test_configure_le_advertisement_sets_local_name_only():
    ble = MagicMock()
    ble.advert.service_UUIDs = ["full-uuid"]
    ble.advert.props = {"org.bluez.LEAdvertisement1": {"Appearance": 833}}
    ble.advert.include_tx_power = True

    mock_constants = MagicMock()
    mock_constants.LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
    with patch.dict(
        sys.modules,
        {"bluezero": MagicMock(), "bluezero.constants": mock_constants},
    ):
        BluezBluetoothBackend._configure_le_advertisement(ble, "NiloCardmed-d212bd98")

    assert ble.advert.local_name == "NiloCardmed-d212bd98"
    assert ble.advert.service_UUIDs == []
    assert ble.advert.include_tx_power is False


def test_max_local_name_length_is_conservative_for_legacy_ad():
    assert MAX_LE_AD_LOCAL_NAME_BYTES >= len("NiloCardmed-d212bd98".encode("utf-8"))
