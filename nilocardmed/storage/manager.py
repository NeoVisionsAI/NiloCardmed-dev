"""Gestión de capturas: cola pending, borrado tras upload y purga por disco."""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nilocardmed.camera.validation import sha256_file, validate_jpeg_file
from nilocardmed.config.models import AppConfig, EnvironmentSettings, StorageSettings
from nilocardmed.ser_client.client import SerClient
from nilocardmed.ser_client.exceptions import SerUploadError
from nilocardmed.ser_client.models import SamplePayload, UploadResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PendingCapture:
    """Captura pendiente de envío a SER."""

    path: Path
    captured_at: datetime
    device_id: str | None = None
    metadata: dict[str, Any] | None = None
    retry_count: int = 0
    next_retry_at: float = 0.0
    sha256: str | None = None

    @property
    def meta_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".meta.json")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "captured_at": self.captured_at.isoformat(),
            "device_id": self.device_id,
            "metadata": self.metadata or {},
            "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "retry_count": self.retry_count,
            "next_retry_at": self.next_retry_at,
        }


class StorageManager:
    """Cola local, borrado tras upload OK y purga de las más antiguas."""

    def __init__(
        self,
        settings: StorageSettings,
        env: EnvironmentSettings,
        *,
        captures_dir: Path | None = None,
    ) -> None:
        self.settings = settings
        self.env = env
        self.captures_dir = captures_dir or (env.data_dir / "captures")
        self.pending_dir = (
            Path(settings.pending_dir)
            if settings.pending_dir
            else env.data_dir / settings.pending_subdir
        )
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self._last_upload_at: float = 0.0

    @property
    def data_root(self) -> Path:
        return self.env.data_dir

    def free_percent(self) -> float:
        usage = shutil.disk_usage(self.data_root)
        if usage.total == 0:
            return 100.0
        return (usage.free / usage.total) * 100.0

    def disk_status(self) -> dict[str, Any]:
        usage = shutil.disk_usage(self.data_root)
        free_pct = (usage.free / usage.total) * 100.0 if usage.total else 100.0
        return {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "free_percent": round(free_pct, 2),
            "min_free_percent": self.settings.min_free_percent,
            "pending_count": len(self.list_pending()),
            "captures_count": len(list(self.captures_dir.glob("*.jpg"))),
        }

    def _load_meta(self, image: Path) -> dict[str, Any]:
        meta_path = image.with_suffix(image.suffix + ".meta.json")
        if not meta_path.exists():
            return {}
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Meta corrupta en %s", meta_path)
            return {}

    def _save_meta(self, image: Path, meta: dict[str, Any]) -> None:
        meta_path = image.with_suffix(image.suffix + ".meta.json")
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def list_pending(self) -> list[PendingCapture]:
        items: list[PendingCapture] = []
        for image in sorted(self.pending_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime):
            meta = self._load_meta(image)
            captured_at = datetime.fromtimestamp(image.stat().st_mtime, tz=UTC)
            if meta.get("captured_at"):
                try:
                    captured_at = datetime.fromisoformat(str(meta["captured_at"]))
                except ValueError:
                    pass
            items.append(
                PendingCapture(
                    path=image,
                    captured_at=captured_at,
                    device_id=meta.get("device_id"),
                    metadata=meta.get("metadata") or {},
                    retry_count=int(meta.get("retry_count") or 0),
                    next_retry_at=float(meta.get("next_retry_at") or 0.0),
                    sha256=meta.get("sha256"),
                )
            )
        items.sort(key=lambda item: item.captured_at)
        return items

    def queue_failed_upload(
        self,
        capture_path: Path,
        *,
        captured_at: datetime,
        device_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Mueve la captura a pending/ para reintento posterior."""
        target = self.pending_dir / capture_path.name
        if capture_path.resolve() != target.resolve():
            if target.exists():
                target.unlink()
            shutil.move(str(capture_path), str(target))

        checksum = sha256_file(target) if target.exists() else None
        meta = {
            "captured_at": captured_at.isoformat(),
            "device_id": device_id,
            "metadata": metadata or {},
            "queued_at": datetime.now(tz=UTC).isoformat(),
            "retry_count": 0,
            "next_retry_at": time.time(),
            "sha256": checksum,
        }
        self._save_meta(target, meta)
        logger.info("Captura encolada en pending: %s (captured_at=%s)", target.name, captured_at.isoformat())
        return target

    def delete_capture(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
            path.with_suffix(path.suffix + ".meta.json").unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("No se pudo eliminar %s: %s", path, exc)

    def _validate_pending_file(self, item: PendingCapture, config: AppConfig) -> bool:
        if not item.path.exists():
            logger.warning("Pending inexistente, eliminando meta: %s", item.path.name)
            item.meta_path.unlink(missing_ok=True)
            return False
        try:
            validate_jpeg_file(item.path, config.camera)
        except Exception as exc:
            logger.error("Pending corrupto %s: %s", item.path.name, exc)
            self.delete_capture(item.path)
            return False
        if item.sha256:
            current = sha256_file(item.path)
            if current != item.sha256:
                logger.error("Checksum mismatch en pending %s", item.path.name)
                self.delete_capture(item.path)
                return False
        return True

    def upload_capture(
        self,
        config: AppConfig,
        capture_path: Path,
        *,
        captured_at: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> UploadResult:
        payload = SamplePayload(
            image_bytes=capture_path.read_bytes(),
            filename=capture_path.name,
            captured_at=captured_at,
            device_id=config.ser.device_id,
            metadata=metadata or {},
        )
        return SerClient(config.ser).upload_sample(payload)

    def handle_after_upload(
        self,
        config: AppConfig,
        capture_path: Path,
        *,
        captured_at: datetime,
        upload: UploadResult | None,
        upload_error: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Devuelve acción tomada: deleted, queued, kept."""
        delete_on_success = (
            config.storage.delete_after_successful_upload
            if config.storage.enabled
            else config.sampling.delete_capture_after_upload
        )
        keep_on_fail = config.sampling.keep_capture_on_upload_failure

        if upload is not None and upload.success:
            if delete_on_success:
                self.delete_capture(capture_path)
                return "deleted"
            return "kept"

        if upload_error or (upload is not None and not upload.success):
            if config.storage.enabled:
                if capture_path.parent.resolve() != self.pending_dir.resolve():
                    self.queue_failed_upload(
                        capture_path,
                        captured_at=captured_at,
                        device_id=config.ser.device_id,
                        metadata=metadata,
                    )
                return "queued"
            if keep_on_fail:
                return "kept"
            self.delete_capture(capture_path)
            return "deleted"

        return "kept"

    def upload_pending_batch(
        self,
        config: AppConfig,
        *,
        max_items: int | None = None,
        window_active: bool = True,
    ) -> dict[str, Any]:
        """Sube pending de forma escalonada; respeta backoff e intervalo mínimo."""
        if not config.storage.enabled or not config.ser.enabled:
            return {"attempted": 0, "uploaded": 0, "failed": 0, "skipped": 0}

        if window_active and config.storage.pending_upload_outside_window_only:
            return {"attempted": 0, "uploaded": 0, "failed": 0, "skipped": 0, "reason": "window_active"}

        limit = max_items or config.storage.pending_upload_max_per_batch
        now = time.time()
        min_gap = config.storage.pending_upload_min_interval_seconds
        if now - self._last_upload_at < min_gap:
            return {"attempted": 0, "uploaded": 0, "failed": 0, "skipped": 0, "reason": "interval"}

        uploaded = 0
        failed = 0
        attempted = 0
        skipped = 0

        for item in self.list_pending():
            if uploaded >= limit:
                break
            if item.next_retry_at > now:
                skipped += 1
                continue
            if not self._validate_pending_file(item, config):
                failed += 1
                continue

            attempted += 1
            try:
                result = self.upload_capture(
                    config,
                    item.path,
                    captured_at=item.captured_at,
                    metadata=item.metadata,
                )
            except SerUploadError as exc:
                logger.warning("Reintento pending fallido %s: %s", item.path.name, exc)
                self._mark_retry(item, config, error=str(exc))
                failed += 1
                self._last_upload_at = time.time()
                break

            if result.success:
                logger.info(
                    "Pending subido %s (captured_at=%s)",
                    item.path.name,
                    item.captured_at.isoformat(),
                )
                self.delete_capture(item.path)
                uploaded += 1
                self._last_upload_at = time.time()
            else:
                self._mark_retry(item, config, error=result.error)
                failed += 1
                self._last_upload_at = time.time()
                break

        return {
            "attempted": attempted,
            "uploaded": uploaded,
            "failed": failed,
            "skipped": skipped,
            "remaining": len(self.list_pending()),
        }

    def upload_pending_during_interval(self, config: AppConfig) -> dict[str, Any]:
        """Un intento de pending en huecos del intervalo de muestreo activo."""
        if not config.storage.enabled:
            return {"uploaded": 0}
        return self.upload_pending_batch(
            config,
            max_items=config.storage.pending_upload_max_per_batch,
            window_active=False,
        )

    def _mark_retry(self, item: PendingCapture, config: AppConfig, *, error: str | None) -> None:
        retry_count = item.retry_count + 1
        base = config.storage.pending_retry_backoff_base_seconds
        max_backoff = config.storage.pending_retry_backoff_max_seconds
        delay = min(base * (2 ** max(retry_count - 1, 0)), max_backoff)
        meta = self._load_meta(item.path)
        meta.update(
            {
                "retry_count": retry_count,
                "next_retry_at": time.time() + delay,
                "last_error": error,
                "captured_at": item.captured_at.isoformat(),
            }
        )
        self._save_meta(item.path, meta)

    def retry_pending(self, config: AppConfig) -> dict[str, Any]:
        """Compatibilidad: sube como máximo un lote configurado."""
        return self.upload_pending_batch(config, window_active=False)

    def enforce_disk_policy(self) -> dict[str, Any]:
        """Si espacio libre < min_free_percent, borra JPG más antiguos de captures/."""
        if not self.settings.enabled or not self.settings.purge_oldest_when_low:
            return {"purged": 0, "free_percent": self.free_percent()}

        free_pct = self.free_percent()
        if free_pct >= self.settings.min_free_percent:
            return {"purged": 0, "free_percent": free_pct}

        purged = 0
        candidates = sorted(self.captures_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime)

        while candidates and self.free_percent() < self.settings.min_free_percent:
            oldest = candidates.pop(0)
            logger.warning(
                "Purga por disco bajo (%.1f%% libre): eliminando capture %s",
                self.free_percent(),
                oldest.name,
            )
            self.delete_capture(oldest)
            purged += 1

        if (
            self.settings.purge_pending_when_low
            and self.free_percent() < self.settings.min_free_percent
        ):
            pending_candidates = sorted(
                self.pending_dir.glob("*.jpg"),
                key=lambda p: p.stat().st_mtime,
            )
            while pending_candidates and self.free_percent() < self.settings.min_free_percent:
                oldest = pending_candidates.pop(0)
                logger.error(
                    "Purga extrema de pending (%.1f%% libre): %s",
                    self.free_percent(),
                    oldest.name,
                )
                self.delete_capture(oldest)
                purged += 1

        return {"purged": purged, "free_percent": round(self.free_percent(), 2)}
