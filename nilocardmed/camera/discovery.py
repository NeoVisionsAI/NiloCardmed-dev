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
_V4L2_DRIVER_NAME = re.compile(r"^\s*Driver name\s*:\s*(.+)$", re.MULTILINE)
_V4L2_BUS_INFO = re.compile(r"^\s*Bus info\s*:\s*(.+)$", re.MULTILINE)
_V4L2_CARD_TYPE = re.compile(r"^\s*Card type\s*:\s*(.+)$", re.MULTILINE)

# Codecs/ISP internos de Raspberry Pi (no son cámaras físicas).
_INTERNAL_V4L2_MARKERS = (
    "bcm2835-codec",
    "bcm2835-isp",
    "bcm2835-codec-decode",
    "vchiq:bcm2835",
    "platform:bcm2835-codec",
    "platform:bcm2835-isp",
)

# Drivers típicos de cámaras USB o CSI reales.
_PHYSICAL_CAMERA_DRIVER_HINTS = (
    "uvcvideo",
    "unicam",
    "bcm2835-v4l2",
    "imx",
    "ov5647",
    "ov9281",
    "libcamera",
)


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


def _identity_blob(device: CameraDevice) -> str:
    return " ".join(
        part.strip().lower()
        for part in (device.name, device.driver, device.bus_info)
        if part
    )


def _parse_v4l2_identity(output: str) -> tuple[str | None, str | None, str | None]:
    driver = _V4L2_DRIVER_NAME.search(output)
    bus = _V4L2_BUS_INFO.search(output)
    card = _V4L2_CARD_TYPE.search(output)
    return (
        driver.group(1).strip() if driver else None,
        bus.group(1).strip() if bus else None,
        card.group(1).strip() if card else None,
    )


def _fetch_v4l2_identity(
    device_path: Path,
    *,
    v4l2_ctl_binary: str,
    timeout: int,
) -> tuple[str | None, str | None, str | None]:
    try:
        result = _run_v4l2_ctl(
            ["-d", str(device_path), "--all"],
            binary=v4l2_ctl_binary,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None, None
    if result.returncode != 0:
        return None, None, None
    return _parse_v4l2_identity(result.stdout)


def is_internal_v4l2_device(device: CameraDevice) -> bool:
    """True si el nodo pertenece a codecs/ISP de plataforma (p. ej. bcm2835 en Pi)."""
    blob = _identity_blob(device)
    return any(marker in blob for marker in _INTERNAL_V4L2_MARKERS)


def is_physical_camera(
    device: CameraDevice,
    *,
    v4l2_identity: tuple[str | None, str | None, str | None] | None = None,
) -> bool:
    """True si el dispositivo parece una cámara USB/CSI real (no codec SoC)."""
    if is_internal_v4l2_device(device):
        return False

    blob = _identity_blob(device)
    if any(hint in blob for hint in _PHYSICAL_CAMERA_DRIVER_HINTS):
        return True
    if "usb-" in blob or blob.startswith("usb"):
        return True

    driver_name, bus_info, _card = v4l2_identity or (None, None, None)
    probe_blob = " ".join(
        part.strip().lower() for part in (driver_name, bus_info) if part
    )
    if any(marker in probe_blob for marker in _INTERNAL_V4L2_MARKERS):
        return False
    if driver_name and driver_name.lower() == "uvcvideo":
        return True
    if bus_info and "usb-" in bus_info.lower():
        return True
    if any(hint in probe_blob for hint in _PHYSICAL_CAMERA_DRIVER_HINTS):
        return True

    return False


def _dedupe_physical_cameras(devices: list[CameraDevice]) -> list[CameraDevice]:
    """Un nodo por cámara física (p. ej. /dev/video0 y /dev/video1 del mismo USB)."""
    grouped: dict[tuple[str, str], CameraDevice] = {}
    for device in devices:
        label = (device.name or str(device.path)).strip().lower()
        bus_key = (device.bus_info or device.driver or label).strip().lower()
        key = (label, bus_key)
        current = grouped.get(key)
        if current is None or device.path.name < current.path.name:
            grouped[key] = device
    return sorted(grouped.values(), key=lambda item: item.path.name)


def list_cameras(
    *,
    device_glob: str = "/dev/video*",
    v4l2_ctl_binary: str = "v4l2-ctl",
    discovery_timeout_seconds: int = 5,
    include_non_capture: bool = False,
    physical_cameras_only: bool = True,
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

    if physical_cameras_only:
        physical: list[CameraDevice] = []
        for device in devices:
            if is_internal_v4l2_device(device):
                continue
            identity: tuple[str | None, str | None, str | None] | None = None
            if not is_physical_camera(device):
                identity = _fetch_v4l2_identity(
                    device.path,
                    v4l2_ctl_binary=v4l2_ctl_binary,
                    timeout=discovery_timeout_seconds,
                )
                if not is_physical_camera(device, v4l2_identity=identity):
                    continue
            if identity and identity[0]:
                driver_name, bus_info, card_type = identity
                device = CameraDevice(
                    path=device.path,
                    name=device.name or card_type,
                    driver=driver_name or device.driver,
                    bus_info=bus_info or device.bus_info,
                    supports_capture=device.supports_capture,
                )
            physical.append(device)
        devices = _dedupe_physical_cameras(physical)

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
