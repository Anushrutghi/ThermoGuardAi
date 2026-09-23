"""Phone browser camera bridge.

The phone (Android/iPhone) opens a webpage which uses the Browser Camera
API / WebRTC via getUserMedia. Frames are streamed over WebSocket to the
backend and pushed into this source's frame buffer; the pipeline reads
from it like any other camera. No mobile app required.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

from camera.base import CameraInfo, CameraSource

logger = logging.getLogger(__name__)


class PhoneCameraSource(CameraSource):
    """Receives frames pushed over WebSocket from a mobile browser."""

    def __init__(self, session_id: str = "phone-1") -> None:
        self._session_id = session_id
        self.info = CameraInfo(
            id=session_id,
            name=f"Mobile Camera ({session_id})",
            kind="phone",
            source="webrtc-bridge",
        )
        self._latest: np.ndarray | None = None
        self._lock = threading.Lock()
        self._connected = False
        self._last_push = 0.0

    @property
    def available(self) -> bool:
        return self._connected and self._latest is not None

    def push_frame(self, jpeg_bytes: bytes) -> None:
        """Decode and buffer a JPEG frame pushed by the browser."""
        import cv2

        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return
        with self._lock:
            self._latest = frame
            self._connected = True
            self._last_push = time.time()

    def read(self) -> np.ndarray | None:
        # stale frames (no push for 3s) mark the phone as disconnected
        if self._connected and time.time() - self._last_push > 3.0:
            self._connected = False
        with self._lock:
            return None if self._latest is None else self._latest.copy()

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False

    def stop(self) -> None:
        self.disconnect()
