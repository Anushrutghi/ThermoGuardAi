"""Detection datatypes and detector interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

# Component classes supported by the platform (YOLO class index 0..N-1)
COMPONENT_CLASSES: list[str] = [
    "circuit_breaker",
    "fuse",
    "relay",
    "contactor",
    "busbar",
    "terminal",
    "cable",
    "mccb",
    "mcb",
    "rccb",
    "transformer",
    "motor_starter",
    "disconnect_switch",
    "power_supply",
    "indicator_light",
    "panel_door",
    "warning_label",
]

CLASS_NAMES: dict[int, str] = {i: name for i, name in enumerate(COMPONENT_CLASSES)}


class HealthStatus(str, Enum):  # noqa: UP042 — str value semantics needed for JSON
    """Component health classification."""

    HEALTHY = "healthy"
    WARNING = "warning"
    HIGH_RISK = "high"
    CRITICAL = "critical"


@dataclass
class Detection:
    """One detected object with metadata."""

    label: str
    confidence: float
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2) pixel coords
    class_id: int = -1
    temperature: float | None = None
    health: HealthStatus = HealthStatus.HEALTHY
    frame_index: int = 0
    heat: Any = None  # CircuitHeatAnalysis (deep core/border/wall heat scan)

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bbox
        return max(0, x2 - x1) * max(0, y2 - y1)


class BaseDetector(ABC):
    """Interface all detectors implement."""

    name: str = "base"

    @abstractmethod
    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Detect electrical components in a BGR frame."""
        raise NotImplementedError

    @property
    def available(self) -> bool:
        """Whether the detector can run (e.g. model file present)."""
        return True

    @property
    def engine(self) -> str:
        """Engine name, e.g. 'ultralytics-yolov11'."""
        return self.name
