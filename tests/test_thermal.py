"""Tests de lectura de temperatura CPU."""

from __future__ import annotations

from nilocardmed.system.thermal import collect_cpu_temperature


def test_collect_cpu_temperature_missing_sysfs(tmp_path):
    result = collect_cpu_temperature(thermal_root=tmp_path)
    assert result["available"] is False
    assert result["celsius"] is None


def test_collect_cpu_temperature_reads_zone(tmp_path):
    zone = tmp_path / "thermal_zone0"
    zone.mkdir()
    (zone / "temp").write_text("48500\n", encoding="utf-8")
    (zone / "type").write_text("cpu-thermal\n", encoding="utf-8")

    result = collect_cpu_temperature(thermal_root=tmp_path)
    assert result["available"] is True
    assert result["celsius"] == 48.5
