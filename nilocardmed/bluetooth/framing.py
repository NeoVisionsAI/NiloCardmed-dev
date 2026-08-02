"""Framing BLE para mensajes JSON que superan el MTU ATT (Web Bluetooth / Android)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from nilocardmed.bluetooth.exceptions import BluetoothProtocolError
from nilocardmed.bluetooth.protocol import CommandRouter
from nilocardmed.config.models import BluetoothSettings

logger = logging.getLogger(__name__)

FRAME_MARKER = "f"


@dataclass
class BleFramer:
    """Fragmenta respuestas y reensambla peticiones multi-frame."""

    enabled: bool = True
    max_notification_bytes: int = 512
    frame_payload_bytes: int = 200
    _rx_total: int | None = field(default=None, init=False, repr=False)
    _rx_parts: dict[int, str] = field(default_factory=dict, init=False, repr=False)

    def reset_rx(self) -> None:
        self._rx_total = None
        self._rx_parts.clear()

    def feed_rx(self, data: bytes) -> bytes | None:
        """Ingiere un write RX; devuelve el mensaje completo o None si faltan frames."""
        if len(data) > self.max_notification_bytes * 64:
            raise BluetoothProtocolError("Petición BLE demasiado grande")

        text = data.decode("utf-8").strip()
        if not text:
            raise BluetoothProtocolError("Mensaje vacío")

        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BluetoothProtocolError("JSON inválido") from exc

        if not isinstance(obj, dict):
            raise BluetoothProtocolError("Se esperaba un objeto JSON")

        if obj.get("t") != FRAME_MARKER:
            self.reset_rx()
            return data

        index = int(obj["i"])
        total = int(obj["n"])
        fragment = str(obj["d"])

        if total < 1 or index < 0 or index >= total:
            raise BluetoothProtocolError("Frame BLE inválido")

        if self._rx_total is None:
            self._rx_total = total
            self._rx_parts.clear()
        elif self._rx_total != total:
            raise BluetoothProtocolError("Secuencia de frames inconsistente")

        self._rx_parts[index] = fragment
        if len(self._rx_parts) < total:
            return None

        message = "".join(self._rx_parts[i] for i in range(total))
        self.reset_rx()
        return message.encode("utf-8")

    def encode_frames(self, payload: bytes) -> list[bytes]:
        """Codifica una respuesta JSON en uno o más frames BLE."""
        if not payload:
            return [b'{"ok":false,"cmd":"","error":"empty_response"}']

        if not self.enabled or len(payload) <= self.max_notification_bytes:
            return [payload]

        fragments = _split_utf8_payload(payload, self.frame_payload_bytes)
        frames: list[bytes] = []
        total = len(fragments)

        for index, fragment in enumerate(fragments):
            frame_obj = {"t": FRAME_MARKER, "i": index, "n": total, "d": fragment}
            frame_bytes = json.dumps(frame_obj, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            if len(frame_bytes) > self.max_notification_bytes:
                raise BluetoothProtocolError(
                    f"Frame BLE {index + 1}/{total} excede max_notification_bytes "
                    f"({len(frame_bytes)} > {self.max_notification_bytes})"
                )
            frames.append(frame_bytes)

        logger.debug("Respuesta BLE fragmentada en %d frames", len(frames))
        return frames


def _split_utf8_payload(payload: bytes, chunk_size: int) -> list[str]:
    """Parte bytes UTF-8 sin cortar caracteres multibyte."""
    if chunk_size < 1:
        raise ValueError("chunk_size debe ser >= 1")

    chunks: list[str] = []
    index = 0
    length = len(payload)
    while index < length:
        end = min(index + chunk_size, length)
        while end > index and (payload[end - 1] & 0xC0) == 0x80:
            end -= 1
        if end == index:
            end = min(index + 1, length)
        chunks.append(payload[index:end].decode("utf-8"))
        index = end
    return chunks


def decode_frames(frames: list[bytes]) -> bytes:
    """Reensambla frames BLE en el JSON de respuesta completo."""
    if not frames:
        return b""

    if len(frames) == 1:
        return _decode_single_frame(frames[0])

    parts: dict[int, str] = {}
    total: int | None = None
    for frame in frames:
        obj = json.loads(frame.decode("utf-8"))
        if obj.get("t") != FRAME_MARKER:
            return frame
        index = int(obj["i"])
        total = int(obj["n"])
        parts[index] = str(obj["d"])

    if total is None or len(parts) != total:
        raise BluetoothProtocolError("Frames BLE incompletos")

    return "".join(parts[i] for i in range(total)).encode("utf-8")


def _decode_single_frame(frame: bytes) -> bytes:
    obj = json.loads(frame.decode("utf-8"))
    if isinstance(obj, dict) and obj.get("t") == FRAME_MARKER:
        return str(obj["d"]).encode("utf-8")
    return frame


class BleTransport:
    """Capa de transporte entre GATT y el router de comandos."""

    def __init__(self, router: CommandRouter, settings: BluetoothSettings) -> None:
        self.router = router
        self.settings = settings
        self.framer = BleFramer(
            enabled=settings.ble_framing_enabled,
            max_notification_bytes=settings.ble_max_notification_bytes,
            frame_payload_bytes=settings.ble_frame_payload_bytes,
        )

    def handle_write(self, data: bytes) -> list[bytes]:
        try:
            message = self.framer.feed_rx(data)
            if message is None:
                return []
            response = self.router.handle_raw(message)
        except BluetoothProtocolError as exc:
            logger.warning("Error de protocolo BLE: %s", exc)
            response = json.dumps(
                {"ok": False, "cmd": "", "error": str(exc)},
                ensure_ascii=False,
            ).encode("utf-8")
        except Exception as exc:
            logger.exception("Error procesando write BLE")
            response = json.dumps(
                {"ok": False, "cmd": "unknown", "error": f"protocol_error: {exc}"},
                ensure_ascii=False,
            ).encode("utf-8")

        return self.framer.encode_frames(response)

    def full_response_from_frames(self, frames: list[bytes]) -> bytes:
        """JSON completo reensamblado (p. ej. para characteristic read)."""
        return decode_frames(frames)
