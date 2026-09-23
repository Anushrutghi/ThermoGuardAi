"""Camera source interfaces and utilities."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class CameraInfo:
    """Static metadata about a camera source."""

    id: str
    name: str
    kind: str  # webcam | usb | file | video | ip | rtsp | mjpeg | phone | rpi
    source: str = ""
    fps: float | None = None
    width: int | None = None
    height: int | None = None


class CameraSource(ABC):
    """Interface implemented by every camera source."""

    info: CameraInfo

    @abstractmethod
    def read(self) -> np.ndarray | None:
        """Return the latest BGR frame or None if unavailable."""
        raise NotImplementedError

    @property
    def available(self) -> bool:
        return True

    def start(self) -> None:
        """Open the source (called before read)."""

    def stop(self) -> None:
        """Release the source."""

    @property
    def kind(self) -> str:
        return self.info.kind
