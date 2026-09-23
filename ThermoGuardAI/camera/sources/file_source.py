"""Offline file source — images (jpg/png) and videos (mp4/avi/mov)."""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from camera.base import CameraInfo, CameraSource

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}


class FileSource(CameraSource):
    """Reads from an image or video file on disk."""

    def __init__(self, path: str, name: str | None = None) -> None:
        p = Path(path)
        ext = p.suffix.lower()
        self._path = p
        self._is_video = ext in _VIDEO_EXTS
        self._cap = None
        self._last_frame: np.ndarray | None = None
        self.info = CameraInfo(
            id=f"file-{p.stem}",
            name=name or f"File: {p.name}",
            kind="video" if self._is_video else "file",
            source=str(p),
        )

    @property
    def available(self) -> bool:
        return self._path.exists()

    def start(self) -> None:
        if not self.available:
            raise FileNotFoundError(f"Source file not found: {self._path}")
        if self._is_video:
            self._cap = cv2.VideoCapture(str(self._path))
            if not self._cap.isOpened():
                raise RuntimeError(f"Could not open video: {self._path}")
        logger.info("File source opened: %s", self._path)

    def read(self) -> np.ndarray | None:
        if not self._is_video:
            if self._last_frame is None:
                if not self.available:
                    return None
                frame = cv2.imread(str(self._path))
                if frame is None:
                    logger.error("Failed to read image %s", self._path)
                    return None
                self._last_frame = frame
            return self._last_frame.copy()

        if self._cap is None:
            self.start()
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok:
            # loop video for continuous inspection
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
        return frame if ok else None

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._last_frame = None
