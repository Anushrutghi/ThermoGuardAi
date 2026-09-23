"""IP camera source — RTSP / MJPEG / HTTP streams via OpenCV."""
from __future__ import annotations

import hashlib
import logging

import cv2
import numpy as np

from camera.base import CameraInfo, CameraSource

logger = logging.getLogger(__name__)


def _stable_id(url: str) -> str:
    """Stable camera id from URL (hash() is salted per-process)."""
    return hashlib.sha256(url.encode()).hexdigest()[:10]


class IpCameraSource(CameraSource):
    """Network camera supporting RTSP, MJPEG and generic HTTP streams."""

    def __init__(self, url: str, name: str | None = None) -> None:
        kind = "rtsp" if url.lower().startswith("rtsp://") else "mjpeg" if "mjpeg" in url.lower() else "ip"
        self.info = CameraInfo(id=f"ip-{_stable_id(url)}", name=name or f"IP Camera ({url[:40]})", kind=kind, source=url)
        self._url = url
        self._cap: cv2.VideoCapture | None = None

    @property
    def available(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def start(self) -> None:
        self._cap = cv2.VideoCapture(self._url)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self._cap.isOpened():
            raise RuntimeError(f"Could not open IP camera stream: {self._url}")
        logger.info("IP camera stream opened: %s", self._url)

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            self.start()
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        return frame if ok else None

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
