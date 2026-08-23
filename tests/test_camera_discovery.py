"""Filtrado de cámaras físicas vs nodos V4L2 internos de plataforma."""

from __future__ import annotations

from pathlib import Path

from nilocardmed.camera.discovery import (
    _dedupe_physical_cameras,
    _fetch_v4l2_identity,
    is_internal_v4l2_device,
    is_physical_camera,
)
from nilocardmed.camera.models import CameraDevice


def test_internal_bcm2835_codec_is_not_physical():
    device = CameraDevice(
        path=Path("/dev/video10"),
        name="bcm2835-codec-decode",
        driver="platform:bcm2835-codec",
        bus_info="platform:bcm2835-codec",
        supports_capture=True,
    )
    assert is_internal_v4l2_device(device) is True
    assert is_physical_camera(device) is False


def test_usb_uvcvideo_is_physical():
    device = CameraDevice(
        path=Path("/dev/video0"),
        name="USB Camera: USB Camera",
        driver="usb-3f980000.usb-1",
        bus_info="usb-3f980000.usb-1",
        supports_capture=True,
    )
    assert is_internal_v4l2_device(device) is False
    assert is_physical_camera(device) is True


def test_usb_detected_from_v4l2_probe():
    device = CameraDevice(path=Path("/dev/video0"), name="video0", supports_capture=True)
    identity = ("uvcvideo", "usb-3f980000.usb-1", "USB Camera: USB Camera")
    assert is_physical_camera(device, v4l2_identity=identity) is True


def test_fetch_v4l2_identity_handles_probe_failure():
    from unittest.mock import patch

    with patch(
        "nilocardmed.camera.discovery._run_v4l2_ctl",
        return_value=type("R", (), {"returncode": 1, "stdout": "", "stderr": "No such device"})(),
    ):
        assert _fetch_v4l2_identity(
            Path("/dev/video0"),
            v4l2_ctl_binary="v4l2-ctl",
            timeout=1,
        ) == (None, None, None)


def test_dedupe_keeps_lowest_video_node_per_usb_camera():
    cam_a0 = CameraDevice(
        path=Path("/dev/video0"),
        name="USB Camera: USB Camera",
        driver="usb-3f980000.usb-1",
        supports_capture=True,
    )
    cam_a1 = CameraDevice(
        path=Path("/dev/video1"),
        name="USB Camera: USB Camera",
        driver="usb-3f980000.usb-1",
        supports_capture=True,
    )
    cam_b = CameraDevice(
        path=Path("/dev/video2"),
        name="Other Cam",
        driver="usb-3f980000.usb-2",
        supports_capture=True,
    )
    result = _dedupe_physical_cameras([cam_a1, cam_b, cam_a0])
    paths = [str(item.path) for item in result]
    assert paths == ["/dev/video0", "/dev/video2"]
