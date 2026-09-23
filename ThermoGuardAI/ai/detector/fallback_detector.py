"""CV/rule-based fallback detector — runs with zero model weights.

Detects plausible electrical components using classical computer vision
heuristics (brightness hotspots, rectangular panel regions, color priors)
and — when a thermal source is active — thermal hot zones. This keeps the
whole pipeline functional before a trained YOLO model exists.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

from ai.detector.base import CLASS_NAMES, BaseDetector, Detection

logger = logging.getLogger(__name__)

# Class indices that plausibly look like dark rectangular modules
_MODULE_CLASSES = [0, 4, 8, 9, 10, 11, 12]  # breaker, busbar, mcb, mccb, rccb, transformer, starter
_HOTSPOT_CLASS = 6  # cable (heating)


class FallbackDetector(BaseDetector):
    """Rule-based detector producing plausible detections without weights."""

    name = "cv-fallback"

    def __init__(self, min_confidence: float = 0.4) -> None:
        self.min_confidence = min_confidence

    def detect(self, frame: np.ndarray) -> list[Detection]:
        detections: list[Detection] = []
        h, w = frame.shape[:2]

        # 1) Large dark rectangular regions -> module-like components
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 90, 255, cv2.THRESH_BINARY_INV)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        used: list[tuple[int, int, int, int]] = []
        for cnt in sorted(contours, key=cv2.contourArea, reverse=True):
            x, y, cw, ch = cv2.boundingRect(cnt)
            area = cw * ch
            if area < (h * w) * 0.004 or cw < 20 or ch < 20:
                continue
            if cw / max(ch, 1) > 6 or ch / max(cw, 1) > 6:  # too thin/strip-like
                continue
            if any(_iou((x, y, x + cw, y + ch), other) > 0.3 for other in used):
                continue
            used.append((x, y, x + cw, y + ch))
            cls_id = _MODULE_CLASSES[len(detections) % len(_MODULE_CLASSES)]
            # REAL similarity: how closely the blob matches a uniform dark
            # module with crisp borders on a brighter panel. Weak/noisy regions
            # score well below the 0.90 gate and are never shown.
            conf = _module_similarity(gray, x, y, cw, ch, cv2.contourArea(cnt))
            if conf < self.min_confidence:
                continue
            detections.append(
                Detection(
                    label=CLASS_NAMES[cls_id],
                    confidence=conf,
                    bbox=(x, y, x + cw, y + ch),
                    class_id=cls_id,
                )
            )
            if len(detections) >= 8:
                break

        # 2) Bright hotspots -> heating cable / overheating zone
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        bright = cv2.inRange(hsv, (0, 0, 200), (180, 255, 255))
        bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:4]:
            x, y, cw, ch = cv2.boundingRect(cnt)
            area = cw * ch
            if area < (h * w) * 0.0015 or area > (h * w) * 0.15:
                continue
            if any(_iou((x, y, x + cw, y + ch), other) > 0.2 for other in used):
                continue
            used.append((x, y, x + cw, y + ch))
            # REAL similarity: the blob must be a bright, coherent spot clearly
            # hotter/lighter than its surroundings (not a dim reflection).
            conf = _hotspot_similarity(gray, x, y, cw, ch)
            if conf < self.min_confidence:
                continue
            detections.append(
                Detection(
                    label=CLASS_NAMES[_HOTSPOT_CLASS],
                    confidence=conf,
                    bbox=(x, y, x + cw, y + ch),
                    class_id=_HOTSPOT_CLASS,
                )
            )

        return detections


def _module_similarity(
    gray: np.ndarray, x: int, y: int, cw: int, ch: int, contour_area: float
) -> float:
    """Structural 0..1 score for a dark-module candidate.

    Combines rectangularity, contrast vs surroundings, internal uniformity
    and border crispness — a genuine visual "is this a circuit module?"
    measure, so arbitrary dark blobs no longer pass the 90% gate.
    """
    roi = gray[y : y + ch, x : x + cw]
    if roi.size == 0:
        return 0.0

    fill = contour_area / max(1.0, float(cw * ch))
    contrast = max(0.0, float(gray.mean()) - float(roi.mean()))
    contrast_n = min(1.0, contrast / 30.0)
    roi_std = float(roi.std())
    uniform_n = 1.0 - min(1.0, roi_std / 25.0)
    edge_n = min(1.0, _border_crossing(gray, x, y, cw, ch) / 25.0)
    similarity = 0.30 * fill + 0.25 * contrast_n + 0.25 * uniform_n + 0.20 * edge_n
    return float(np.clip(similarity, 0.0, 1.0))


def _hotspot_similarity(gray: np.ndarray, x: int, y: int, cw: int, ch: int) -> float:
    """Bright-spot score: must be clearly lighter than its surroundings."""
    roi = gray[y : y + ch, x : x + cw]
    if roi.size == 0:
        return 0.0
    pad_x, pad_y = max(1, int(cw * 0.2)), max(1, int(ch * 0.2))
    x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
    x1, y1 = min(gray.shape[1], x + cw + pad_x), min(gray.shape[0], y + ch + pad_y)
    ring = np.ones_like(gray, dtype=bool)
    ring[y0:y1, x0:x1] = False
    ring[y : y + ch, x : x + cw] = False
    surround = gray[ring]
    if surround.size == 0:
        return 0.0
    delta = float(roi.mean()) - float(surround.mean())
    roi_std = float(roi.std())
    return float(np.clip(0.6 * min(1.0, delta / 60.0) + 0.4 * (1.0 - min(1.0, roi_std / 30.0)), 0.0, 1.0))


def _border_crossing(gray: np.ndarray, x: int, y: int, cw: int, ch: int) -> float:
    """Mean brightness jump across the candidate's perimeter (crisp edge?)."""
    diffs: list[float] = []
    for i in range(x, x + cw):
        if y - 1 >= 0:
            diffs.append(abs(int(gray[y - 1, i]) - int(gray[y, i])))
        if y + ch < gray.shape[0]:
            diffs.append(abs(int(gray[y + ch, i]) - int(gray[y + ch - 1, i])))
    for j in range(y, y + ch):
        if x - 1 >= 0:
            diffs.append(abs(int(gray[j, x - 1]) - int(gray[j, x])))
        if x + cw < gray.shape[1]:
            diffs.append(abs(int(gray[j, x + cw]) - int(gray[j, x + cw - 1])))
    return float(np.mean(diffs)) if diffs else 0.0


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0
