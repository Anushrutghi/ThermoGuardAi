"""Live positioning guidance for the switch-first inspection.

While the camera runs, the system evaluates switch size/distance, centering,
sharpness, lighting and partial visibility, and gives the user concrete,
real-time instructions ("Move closer", "Move slightly left", "Hold camera
steady", ...). The inspection only proceeds when the required conditions are
acceptable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class GuidanceResult:
    """Result of one positioning evaluation."""

    messages: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)  # switch|distance|centering|sharpness|lighting
    ready: bool = False
    score: int = 0  # 0..100 readiness score

    def to_dict(self) -> dict:
        return {
            "messages": self.messages,
            "checks": self.checks,
            "ready": self.ready,
            "score": self.score,
        }


def evaluate_position(
    frame: np.ndarray,
    bbox: tuple[int, int, int, int],
    size_min: float = 0.08,
    size_max: float = 0.45,
    center_tolerance: float = 0.30,
    min_brightness: float = 45.0,
    min_sharpness: float = 35.0,
) -> GuidanceResult:
    """Evaluate how well the switch is positioned for a scan.

    `bbox` is the switch box in pixel space; `frame` the current RGB frame.
    """
    h, w = frame.shape[:2]
    if h == 0 or w == 0 or bbox is None:
        return GuidanceResult(messages=["Point the camera toward an electrical switch."], checks={"switch": False}, ready=False, score=0)

    x1, y1, x2, y2 = bbox
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return GuidanceResult(messages=["Point the camera toward an electrical switch."], checks={"switch": False}, ready=False, score=0)

    box_h = y2 - y1
    cx = (x1 + x2) / 2 / w
    cy = (y1 + y2) / 2 / h
    size_frac = box_h / h

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    roi = gray[y1:y2, x1:x2]
    brightness = float(roi.mean()) if roi.size else 0.0
    sharpness = float(cv2.Laplacian(roi, cv2.CV_64F).var()) if roi.size > 3 else 0.0

    checks: dict[str, bool] = {"switch": True}
    messages: list[str] = []
    score = 0.0

    # --- distance ---
    if size_frac < size_min:
        messages.append("Move camera closer")
        checks["distance"] = False
    elif size_frac > size_max:
        messages.append("Move camera farther away")
        checks["distance"] = False
    else:
        checks["distance"] = True
        score += 30

    # --- centering ---
    off_x = cx - 0.5
    off_y = cy - 0.5
    dist = (off_x**2 + off_y**2) ** 0.5
    # partially visible = box touches the frame edge
    touches_edge = x1 <= 2 or y1 <= 2 or x2 >= w - 2 or y2 >= h - 2
    if touches_edge:
        messages.append("Switch partially visible — center it in the frame")
        checks["centering"] = False
    elif dist > center_tolerance:
        checks["centering"] = False
        if abs(off_x) > abs(off_y):
            messages.append("Move slightly left" if off_x > 0 else "Move slightly right")
        else:
            messages.append("Move slightly up" if off_y > 0 else "Move slightly down")
    else:
        checks["centering"] = True
        score += 25

    # --- sharpness / stability ---
    if sharpness < min_sharpness:
        messages.append("Hold camera steady — image is blurry")
        checks["sharpness"] = False
    else:
        checks["sharpness"] = True
        score += 25

    # --- lighting ---
    if brightness < min_brightness:
        messages.append("Insufficient lighting — improve lighting on the switch")
        checks["lighting"] = False
    else:
        checks["lighting"] = True
        score += 20

    if not messages:
        messages.append("✓ Switch positioned correctly")
    ready = all(checks.values()) and len(checks) >= 4
    return GuidanceResult(
        messages=messages,
        checks=checks,
        ready=ready,
        score=min(100, round(score)),
    )
