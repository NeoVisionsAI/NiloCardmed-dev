"""Tests del framing BLE."""

from __future__ import annotations

import json

from nilocardmed.bluetooth.framing import BleFramer, decode_frames


def test_encode_single_frame_when_small():
    framer = BleFramer(enabled=True, max_notification_bytes=512, frame_payload_bytes=200)
    payload = b'{"ok":true,"cmd":"ping"}'
    frames = framer.encode_frames(payload)
    assert frames == [payload]


def test_encode_and_decode_multi_frame():
    framer = BleFramer(enabled=True, max_notification_bytes=160, frame_payload_bytes=60)
    original = {"ok": True, "cmd": "test", "data": {"x": "y" * 300}}
    payload = json.dumps(original, ensure_ascii=False).encode()
    frames = framer.encode_frames(payload)
    assert len(frames) > 1
    decoded = json.loads(decode_frames(frames).decode())
    assert decoded == original


def test_rx_reassembly():
    framer = BleFramer(enabled=True, max_notification_bytes=80, frame_payload_bytes=30)
    payload = json.dumps(
        {"cmd": "auth", "password": "x" * 120},
        ensure_ascii=False,
    ).encode()
    frames = framer.encode_frames(payload)
    assert len(frames) >= 2
    complete = None
    for frame in frames:
        complete = framer.feed_rx(frame)
    assert complete == payload
