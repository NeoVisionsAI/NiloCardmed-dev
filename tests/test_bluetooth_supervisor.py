"""Tests del supervisor BLE."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from nilocardmed.bluetooth.supervisor import BluetoothSupervisor
from nilocardmed.config.models import AppConfig, ResilienceSettings


def test_supervisor_restarts_unhealthy_bluetooth():
    config = AppConfig(
        resilience=ResilienceSettings(
            bluetooth_health_check_interval_seconds=5,
            bluetooth_restart_cooldown_seconds=10,
        )
    )
    config_manager = MagicMock()
    config_manager.get.return_value = config

    bluetooth_service = MagicMock()
    bluetooth_service.is_healthy.side_effect = [False, True]
    bluetooth_service.has_active_client.return_value = False
    bluetooth_service.is_publish_alive.return_value = False

    shutdown = threading.Event()
    supervisor = BluetoothSupervisor(config_manager, bluetooth_service)

    calls = {"wait": 0}

    def wait_once(timeout: float) -> bool:
        calls["wait"] += 1
        if calls["wait"] >= 1:
            shutdown.set()
            return True
        return False

    shutdown.wait = wait_once  # type: ignore[method-assign]
    supervisor._last_restart = 0.0
    supervisor._skip_startup_grace = True

    supervisor.run(shutdown)

    assert bluetooth_service.restart.call_count == 1
    assert supervisor.restart_count == 1


def test_supervisor_keeps_discoverable_without_gatt_restart():
    config = AppConfig(
        resilience=ResilienceSettings(
            bluetooth_supervisor_enabled=False,
            bluetooth_keep_discoverable_enabled=True,
            bluetooth_health_check_interval_seconds=5,
        )
    )
    config_manager = MagicMock()
    config_manager.get.return_value = config

    bluetooth_service = MagicMock()
    bluetooth_service.is_healthy.return_value = False
    bluetooth_service.has_active_client.return_value = False

    shutdown = threading.Event()

    def stop_after_one(timeout: float) -> bool:
        shutdown.set()
        return True

    shutdown.wait = stop_after_one  # type: ignore[method-assign]

    supervisor = BluetoothSupervisor(config_manager, bluetooth_service)
    supervisor._skip_startup_grace = True
    supervisor.run(shutdown)

    bluetooth_service.ensure_adapter_visibility.assert_called()
    bluetooth_service.restart.assert_not_called()


def test_supervisor_skips_when_disabled():
    config = AppConfig(
        resilience=ResilienceSettings(
            bluetooth_supervisor_enabled=False,
            bluetooth_keep_discoverable_enabled=False,
            bluetooth_health_check_interval_seconds=5,
        )
    )
    config_manager = MagicMock()
    config_manager.get.return_value = config

    bluetooth_service = MagicMock()
    bluetooth_service.is_healthy.return_value = False

    shutdown = threading.Event()

    def stop_after_one(timeout: float) -> bool:
        shutdown.set()
        return True

    shutdown.wait = stop_after_one  # type: ignore[method-assign]

    supervisor = BluetoothSupervisor(config_manager, bluetooth_service)
    supervisor._skip_startup_grace = True
    supervisor.run(shutdown)

    bluetooth_service.restart.assert_not_called()


def test_supervisor_skips_restart_with_active_client():
    config = AppConfig(resilience=ResilienceSettings(bluetooth_restart_cooldown_seconds=10))
    config_manager = MagicMock()
    config_manager.get.return_value = config

    bluetooth_service = MagicMock()
    bluetooth_service.is_healthy.return_value = False
    bluetooth_service.has_active_client.return_value = True
    bluetooth_service.is_publish_alive.return_value = True

    shutdown = threading.Event()

    def stop_after_one(timeout: float) -> bool:
        shutdown.set()
        return True

    shutdown.wait = stop_after_one  # type: ignore[method-assign]

    supervisor = BluetoothSupervisor(config_manager, bluetooth_service)
    supervisor._skip_startup_grace = True
    supervisor.run(shutdown)

    bluetooth_service.restart.assert_not_called()
