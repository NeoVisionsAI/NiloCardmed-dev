"""Registro en memoria de ciclos y eventos (diagnóstico BLE) con persistencia JSONL."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nilocardmed.sampler.models import SampleCycleResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EventRecord:
    """Evento del sistema (WiFi, storage, watchdog…)."""

    timestamp_epoch: float
    name: str
    message: str
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "timestamp_epoch": self.timestamp_epoch,
            "name": self.name,
            "message": self.message,
        }
        if self.data is not None:
            body["data"] = self.data
        return body


class TelemetryStore:
    """Buffer circular thread-safe de ciclos y eventos."""

    def __init__(
        self,
        *,
        max_cycles: int = 50,
        max_events: int = 100,
        persist_path: Path | None = None,
        max_persist_bytes: int = 5_000_000,
    ) -> None:
        self._cycles: deque[dict[str, Any]] = deque(maxlen=max_cycles)
        self._events: deque[EventRecord] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._last_success_at: float | None = None
        self._last_sampler_tick_at: float | None = None
        self._sampler_pause_reason: str | None = None
        self._persist_path = persist_path
        self._max_persist_bytes = max_persist_bytes

    @property
    def started_at_epoch(self) -> float:
        return self._started_at

    @property
    def last_success_at_epoch(self) -> float | None:
        return self._last_success_at

    @property
    def last_sampler_tick_at_epoch(self) -> float | None:
        return self._last_sampler_tick_at

    @property
    def sampler_pause_reason(self) -> str | None:
        return self._sampler_pause_reason

    def configure_persistence(self, path: Path) -> None:
        self._persist_path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def uptime_seconds(self) -> float:
        return time.time() - self._started_at

    def record_sampler_tick(self, *, pause_reason: str | None = None) -> None:
        with self._lock:
            self._last_sampler_tick_at = time.time()
            self._sampler_pause_reason = pause_reason

    def record_cycle(self, result: SampleCycleResult) -> None:
        entry = {
            "timestamp_epoch": time.time(),
            "type": "cycle",
            **result.to_dict(),
        }
        with self._lock:
            self._cycles.appendleft(entry)
            self._last_sampler_tick_at = time.time()
            if result.success:
                self._last_success_at = time.time()
        self._persist_entry(entry)

    def record_event(
        self,
        name: str,
        message: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> None:
        record = EventRecord(
            timestamp_epoch=time.time(),
            name=name,
            message=message,
            data=data,
        )
        entry = {"type": "event", **record.to_dict()}
        with self._lock:
            self._events.appendleft(record)
        self._persist_entry(entry)

    def get_cycles(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._cycles)[:limit]

    def get_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [event.to_dict() for event in list(self._events)[:limit]]

    def capture_stats(self) -> dict[str, Any]:
        with self._lock:
            cycles = list(self._cycles)
            last_success = self._last_success_at
            last_tick = self._last_sampler_tick_at
        successful = sum(1 for cycle in cycles if cycle.get("success"))
        return {
            "cycles_recorded": len(cycles),
            "cycles_successful": successful,
            "last_success_at_epoch": last_success,
            "last_sampler_tick_at_epoch": last_tick,
        }

    def load_recent_from_disk(self, limit: int = 100) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            lines = self._persist_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("No se pudo leer telemetría persistida: %s", exc)
            return

        loaded = 0
        for line in reversed(lines[-limit:]):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") == "event":
                record = EventRecord(
                    timestamp_epoch=float(entry["timestamp_epoch"]),
                    name=str(entry["name"]),
                    message=str(entry["message"]),
                    data=entry.get("data"),
                )
                with self._lock:
                    self._events.appendleft(record)
                loaded += 1
            elif entry.get("type") == "cycle":
                with self._lock:
                    self._cycles.appendleft(entry)
                loaded += 1
        if loaded:
            logger.info("Telemetría restaurada desde disco (%s entradas)", loaded)

    def _persist_entry(self, entry: dict[str, Any]) -> None:
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self._persist_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._rotate_if_needed()
        except OSError as exc:
            logger.debug("No se pudo persistir telemetría: %s", exc)

    def _rotate_if_needed(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            if self._persist_path.stat().st_size <= self._max_persist_bytes:
                return
            backup = self._persist_path.with_suffix(".jsonl.old")
            if backup.exists():
                backup.unlink()
            self._persist_path.replace(backup)
            self._persist_path.touch()
        except OSError as exc:
            logger.warning("Rotación de telemetría fallida: %s", exc)


telemetry = TelemetryStore()
