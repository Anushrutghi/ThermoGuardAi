"""Camera source factory and discovery."""
from __future__ import annotations

import logging

from camera.base import CameraSource
from camera.sources.file_source import FileSource
from camera.sources.ip_camera import IpCameraSource
from camera.sources.phone import PhoneCameraSource
from camera.sources.rpi_camera import RpiCameraSource
from camera.sources.webcam import WebcamSource

logger = logging.getLogger(__name__)


def create_camera(kind: str, source: str | None = None, **kwargs) -> CameraSource:
    """Create a camera source from a kind string and optional source spec."""
    kind = (kind or "webcam").lower()
    if kind in ("webcam", "usb"):
        index = int(source) if source and source.isdigit() else 0
        return WebcamSource(index=index, **kwargs)
    if kind in ("file", "image"):
        return FileSource(source or "", **kwargs)
    if kind in ("video",):
        return FileSource(source or "", **kwargs)
    if kind in ("ip", "rtsp", "mjpeg"):
        if not source:
            raise ValueError("IP camera requires a URL")
        return IpCameraSource(source, **kwargs)
    if kind == "rpi":
        return RpiCameraSource(**kwargs)
    if kind == "phone":
        return PhoneCameraSource(session_id=kwargs.get("session_id", "phone-1"))
    raise ValueError(f"Unknown camera kind: {kind}")


def discover_webcams(max_index: int = 4) -> list[WebcamSource]:
    """Probe common indices to find attached webcams/USB cameras."""
    import cv2

    found: list[WebcamSource] = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            cap.release()
            found.append(WebcamSource(index=i))
    return found


def camera_from_spec(spec: dict) -> CameraSource:
    """Create a camera from a config dict: {kind, source, name, ...}."""
    return create_camera(spec.get("kind", "webcam"), spec.get("source"), name=spec.get("name"))
