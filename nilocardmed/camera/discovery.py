"""Descubrimiento de dispositivos de vídeo V4L2."""

from __future__ import annotations

import glob
import logging
import re
import subprocess
from pathlib import Path

from nilocardmed.camera.exceptions import CameraError
from nilocardmed.camera.models import CameraDevice

logger = logging.getLogger(__name__)

_DEVICE_HEADER = re.compile(r"^(.+) \((.+)\):$")
_DEVICE_NODE = re.compile(r"^\s+/dev/video(\d+)")


def _run_v4l2_ctl(args: list[str], *, binary: str, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _supports_capture(device_path: Path, *, v4l2_ctl_binary: str, timeout: int) -> bool:
    try:
        result = _run_v4l2_ctl(
            ["-d", str(device_path), "--all"],
            binary=v4l2_ctl_binary,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return True

    if result.returncode != 0:
        logger.debug(
            "v4l2-ctl no pudo inspeccionar %s: %s",
            device_path,
            result.stderr.strip(),
        )
        return False
    return "Video Capture" in result.stdout


def _parse_v4l2_devices(output: str) -> dict[str, CameraDevice]:
    devices: dict[str, CameraDevice] = {}
    current_name: str | None = None
    current_driver: str | None = None
    current_bus: str | None = None

    for line in output.splitlines():
        header = _DEVICE_HEADER.match(line.strip())
        if header:
            current_name = header.group(1).strip()
            current_driver = header.group(2).strip()
            current_bus = None
            continue

        if line.strip().startswith("Bus info"):
            current_bus = line.split(":", 1)[-1].strip()
            continue

        node = _DEVICE_NODE.match(line)
        if not node or current_name is None:
            continue

        path = Path(f"/dev/video{node.group(1)}")
        devices[str(path)] = CameraDevice(
            path=path,
            name=current_name,
            driver=current_driver,
            bus_info=current_bus,
            supports_capture=True,
        )

    return devices


def list_cameras(
    *,
    device_glob: str = "/dev/video*",
    v4l2_ctl_binary: str = "v4l2-ctl",
    discovery_timeout_seconds: int = 5,
    include_non_capture: bool = False,
) -> list[CameraDevice]:
    """Lista cámaras disponibles, enriqueciendo con metadatos de v4l2-ctl."""
    metadata: dict[str, CameraDevice] = {}

    try:
        result = _run_v4l2_ctl(
            ["--list-devices"],
            binary=v4l2_ctl_binary,
            timeout=discovery_timeout_seconds,
        )
        if result.returncode == 0:
            metadata = _parse_v4l2_devices(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("No se pudo ejecutar v4l2-ctl (%s); usando glob de dispositivos", exc)

    discovered: dict[str, CameraDevice] = {}
    for node in sorted(glob.glob(device_glob)):
        path = Path(node)
        if not path.exists():
            continue

        key = str(path)
        if key in metadata:
            device = metadata[key]
        else:
            device = CameraDevice(
                path=path,
                name=path.name,
                supports_capture=_supports_capture(
                    path,
                    v4l2_ctl_binary=v4l2_ctl_binary,
                    timeout=discovery_timeout_seconds,
                ),
            )

        if key not in metadata:
            discovered[key] = device
            continue

        discovered[key] = CameraDevice(
            path=device.path,
            name=device.name,
            driver=device.driver,
            bus_info=device.bus_info,
            supports_capture=_supports_capture(
                path,
                v4l2_ctl_binary=v4l2_ctl_binary,
                timeout=discovery_timeout_seconds,
            ),
        )

    devices = [
        device
        for device in discovered.values()
        if include_non_capture or device.supports_capture
    ]
    devices.sort(key=lambda item: item.path.name)
    return devices


def resolve_device(
    device_path: str | None,
    *,
    device_glob: str = "/dev/video*",
    v4l2_ctl_binary: str = "v4l2-ctl",
    discovery_timeout_seconds: int = 5,
) -> CameraDevice:
    """Resuelve la cámara a usar (explícita o la primera disponible)."""
    cameras = list_cameras(
        device_glob=device_glob,
        v4l2_ctl_binary=v4l2_ctl_binary,
        discovery_timeout_seconds=discovery_timeout_seconds,
    )
    if not cameras:
        raise CameraError("No se detectaron cámaras de captura")

    if device_path is None:
        return cameras[0]

    requested = Path(device_path)
    for camera in cameras:
        if camera.path == requested:
            return camera

    raise CameraError(f"Cámara no encontrada: {device_path}")
