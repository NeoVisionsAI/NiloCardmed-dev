"""Tests de lectura de batería / power_supply."""

from __future__ import annotations

from nilocardmed.system.power import collect_battery_status


def test_collect_battery_status_empty_dir(tmp_path):
    result = collect_battery_status(power_supply_root=tmp_path)
    assert result["available"] is False
    assert result["sources"] == []
    assert result["power_source"] == "usb"
    assert result["display_percent"] == 100


def test_collect_battery_status_reads_battery_entry(tmp_path):
    supply = tmp_path / "battery"
    supply.mkdir()
    (supply / "type").write_text("Battery\n", encoding="utf-8")
    (supply / "status").write_text("Discharging\n", encoding="utf-8")
    (supply / "capacity").write_text("73\n", encoding="utf-8")

    result = collect_battery_status(power_supply_root=tmp_path)
    assert result["available"] is True
    assert result["level_percent"] == 73
    assert result["primary"]["name"] == "battery"
    assert result["status"] == "Discharging"
    assert result["power_source"] == "powerbank"
    assert result["display_percent"] == 73
    assert result["on_battery"] is True


def test_collect_battery_status_mains_shows_full_charge(tmp_path):
    supply = tmp_path / "mains"
    supply.mkdir()
    (supply / "type").write_text("Mains\n", encoding="utf-8")
    (supply / "online").write_text("1\n", encoding="utf-8")

    result = collect_battery_status(power_supply_root=tmp_path)
    assert result["power_source"] == "mains"
    assert result["display_percent"] == 100
    assert result["source_label"] == "Corriente"
