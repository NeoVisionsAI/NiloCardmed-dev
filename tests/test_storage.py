"""Tests de StorageManager."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from nilocardmed.config.models import AppConfig, EnvironmentSettings, StorageSettings
from nilocardmed.ser_client.models import UploadResult
from nilocardmed.storage.manager import StorageManager


@pytest.fixture
def storage_env(tmp_path: Path):
    env = EnvironmentSettings().model_copy(update={"data_dir": tmp_path})
    settings = StorageSettings()
    captures = tmp_path / "captures"
    return StorageManager(settings, env, captures_dir=captures)


def _write_jpg(path: Path, size: tuple[int, int] = (640, 480)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color=(40, 80, 120))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    path.write_bytes(buffer.getvalue())


def test_delete_after_successful_upload(storage_env: StorageManager):
    config = AppConfig()
    capture = storage_env.captures_dir / "sample.jpg"
    _write_jpg(capture)
    captured_at = datetime.now(tz=UTC)
    upload = UploadResult(
        success=True,
        status_code=201,
        attempts=1,
        elapsed_ms=10.0,
        url="http://ser/test",
    )

    action = storage_env.handle_after_upload(
        config,
        capture,
        captured_at=captured_at,
        upload=upload,
        upload_error=None,
    )

    assert action == "deleted"
    assert not capture.exists()


def test_queue_on_upload_failure(storage_env: StorageManager):
    config = AppConfig()
    capture = storage_env.captures_dir / "fail.jpg"
    _write_jpg(capture)
    captured_at = datetime(2025, 6, 15, 10, 0, 15, tzinfo=UTC)

    action = storage_env.handle_after_upload(
        config,
        capture,
        captured_at=captured_at,
        upload=None,
        upload_error="connection refused",
    )

    assert action == "queued"
    pending = storage_env.pending_dir / "fail.jpg"
    assert pending.exists()
    assert not capture.exists()
    meta = json.loads(pending.with_suffix(".jpg.meta.json").read_text())
    assert meta["captured_at"] == captured_at.isoformat()
    assert meta.get("sha256")


def test_enforce_disk_policy_purges_captures_not_pending(storage_env: StorageManager):
    pending = storage_env.pending_dir / "old.jpg"
    capture = storage_env.captures_dir / "new.jpg"
    _write_jpg(pending)
    _write_jpg(capture)

    usage = type("Usage", (), {"total": 1000, "used": 950, "free": 50})()

    with patch("nilocardmed.storage.manager.shutil.disk_usage", return_value=usage):
        result = storage_env.enforce_disk_policy()

    assert result["purged"] >= 1
    assert pending.exists()
    assert not capture.exists()


def test_retry_pending_deletes_on_success(storage_env: StorageManager):
    config = AppConfig()
    pending = storage_env.pending_dir / "retry.jpg"
    _write_jpg(pending)
    captured_at = datetime(2025, 6, 14, 8, 30, 0, tzinfo=UTC)
    pending.with_suffix(".jpg.meta.json").write_text(
        json.dumps(
            {
                "captured_at": captured_at.isoformat(),
                "next_retry_at": 0,
                "retry_count": 0,
            }
        ),
        encoding="utf-8",
    )

    upload_ok = UploadResult(
        success=True,
        status_code=201,
        attempts=1,
        elapsed_ms=5.0,
        url="http://ser/test",
    )

    with patch.object(StorageManager, "upload_capture", return_value=upload_ok) as mocked:
        result = storage_env.upload_pending_batch(config, window_active=False)
        mocked.assert_called_once()
        args, kwargs = mocked.call_args
        assert kwargs["captured_at"] == captured_at

    assert result["uploaded"] == 1
    assert result["remaining"] == 0
    assert not pending.exists()


def test_upload_pending_respects_interval(storage_env: StorageManager):
    config = AppConfig()
    pending = storage_env.pending_dir / "slow.jpg"
    _write_jpg(pending)
    pending.with_suffix(".jpg.meta.json").write_text(
        json.dumps({"captured_at": datetime.now(tz=UTC).isoformat(), "next_retry_at": 0}),
        encoding="utf-8",
    )

    upload_ok = UploadResult(
        success=True,
        status_code=201,
        attempts=1,
        elapsed_ms=5.0,
        url="http://ser/test",
    )

    with patch.object(StorageManager, "upload_capture", return_value=upload_ok):
        first = storage_env.upload_pending_batch(config, window_active=False)
        second = storage_env.upload_pending_batch(config, window_active=False)

    assert first["uploaded"] == 1
    assert second["uploaded"] == 0
    assert second.get("reason") == "interval"
