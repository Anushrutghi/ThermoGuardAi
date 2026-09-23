"""Raspberry Pi camera module source (picamera2)."""
from __future__ import annotations

import logging

import numpy as np

from camera.base import CameraInfo, CameraSource

logger = logging.getLogger(__name__)


class RpiCameraSource(CameraSource):
    """Raspberry Pi Camera Module (CSI) via picamera2."""

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 30) -> None:
        self.info = CameraInfo(
            id="rpi-cam",
            name="Raspberry Pi Camera",
            kind="rpi",
            source="picamera2",
            fps=float(fps),
            width=width,
            height=height,
        )
        self._cam = None

    @property
    def available(self) -> bool:
        return self._cam is not None

    def start(self) -> None:
        try:
            from picamera2 import Picamera2  # type: ignore

            cam = Picamera2()
            config = cam.create_still_configuration(
                main={"size": (self.info.width or 1280, self.info.height or 720)}
            )
            cam.configure(config)
            cam.start()
            self._cam = cam
            logger.info("Raspberry Pi camera started")
        except Exception as exc:  # pragma: no cover - hardware dependent
            raise RuntimeError(f"Raspberry Pi camera unavailable: {exc}") from exc

    def read(self) -> np.ndarray | None:  # pragma: no cover - hardware dependent
        if self._cam is None:
            return None
        arr = self._cam.capture_array()
        return np.asarray(arr)

    def stop(self) -> None:  # pragma: no cover - hardware dependent
        if self._cam is not None:
            self._cam.stop()
            self._cam = None
