"""Capture manager — owns the active camera source and capturer.

Allows switching sources at runtime and exposes the latest frame to
inspection services and dashboards.
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from camera.base import CameraSource
from camera.factory import create_camera, discover_webcams
from camera.frame_buffer import BackgroundCapturer

logger = logging.getLogger(__name__)


class CaptureManager:
    """Manages the currently active camera source."""

    def __init__(self, target_fps: float | None = None) -> None:
        from backend.core.config import get_settings

        self._lock = threading.Lock()
        self._capturer: BackgroundCapturer | None = None
        self._target_fps = target_fps or get_settings().capture_target_fps

    # ------------------------------------------------------------------
    def switch(self, source: CameraSource) -> None:
        """Stop the current source (if any) and start a new one."""
        with self._lock:
            self._stop_locked()
            capturer = BackgroundCapturer(source, target_fps=self._target_fps)
            try:
                capturer.start()
            except Exception:
                logger.exception("Failed to start camera %s", source.info.id)
                raise
            self._capturer = capturer
            logger.info("Switched camera to %s (%s)", source.info.name, source.info.kind)

    def switch_to(self, kind: str, source: str | None = None, **kwargs) -> None:
        self.switch(create_camera(kind, source, **kwargs))

    # ------------------------------------------------------------------
    def latest_frame(self) -> np.ndarray | None:
        """Return the latest captured frame or None."""
        with self._lock:
            if self._capturer is None:
                return None
            entry = self._capturer.buffer.latest()
        return None if entry is None else entry[1]

    def read_frame(self) -> np.ndarray | None:
        return self.latest_frame()

    @property
    def active_id(self) -> str | None:
        with self._lock:
            return self._capturer._source.info.id if self._capturer else None  # noqa: SLF001

    @property
    def measured_fps(self) -> float:
        with self._lock:
            return self._capturer.measured_fps if self._capturer else 0.0

    def is_active(self) -> bool:
        with self._lock:
            return self._capturer is not None

    def stop(self) -> None:
        """Stop and release the active camera source (frees the webcam)."""
        with self._lock:
            self._stop_locked()

    # ------------------------------------------------------------------
    def available_cameras(self) -> list[dict]:
        """List discoverable cameras (webcams + registered ones)."""
        sources: list[dict] = [{"id": s.info.id, "name": s.info.name, "kind": s.info.kind} for s in discover_webcams()]
        sources.append({"id": "phone-1", "name": "Mobile Phone (browser)", "kind": "phone"})
        sources.append({"id": "file-upload", "name": "Uploaded Image / Video", "kind": "file"})
        sources.append({"id": "ip-rtsp", "name": "IP / RTSP Camera", "kind": "ip"})
        sources.append({"id": "rpi-cam", "name": "Raspberry Pi Camera", "kind": "rpi"})
        return sources

    def _stop_locked(self) -> None:
        if self._capturer is not None:
            try:
                self._capturer.stop()
            except Exception:
                logger.exception("Error stopping capturer")
            self._capturer = None
