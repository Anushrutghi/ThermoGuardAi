"""Webcam / USB camera source via OpenCV."""
from __future__ import annotations

import logging

import cv2
import numpy as np

from camera.base import CameraInfo, CameraSource

logger = logging.getLogger(__name__)


class WebcamSource(CameraSource):
    """Local webcam / USB camera using cv2.VideoCapture."""

    def __init__(self, index: int = 0, name: str | None = None, width: int = 1280, height: int = 720, fps: int = 30) -> None:
        self.info = CameraInfo(
            id=f"webcam-{index}",
            name=name or f"Webcam {index}",
            kind="webcam",
            source=str(index),
            fps=float(fps),
            width=width,
            height=height,
        )
        self._index = index
        self._width, self._height, self._fps = width, height, fps
        self._cap: cv2.VideoCapture | None = None

    @property
    def available(self) -> bool:
        if self._cap is None:
            return False
        return self._cap.isOpened()

    def start(self) -> None:
        cap = cv2.VideoCapture(self._index, cv2.CAP_DSHOW if _is_windows() else cv2.CAP_ANY)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        cap.set(cv2.CAP_PROP_FPS, self._fps)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open camera {self._index}")
        self._cap = cap
        logger.info("Webcam %d opened", self._index)

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            self.start()
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok:
            return None
        return frame

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


def _is_windows() -> bool:
    import platform

    return platform.system() == "Windows"
