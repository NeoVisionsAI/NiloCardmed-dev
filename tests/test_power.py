"""Tests de lectura de batería / power_supply."""

from __future__ import annotations

from nilocardmed.system.power import collect_battery_status


def test_collect_battery_status_empty_dir(tmp_path):
    result = collect_battery_status(power_supply_root=tmp_path)
    assert result["available"] is False
    assert result["sources"] == []
    assert "message" in result


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
