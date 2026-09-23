"""Wall-switch detector — S1 switch-first inspection.

The S1 workflow is: the camera first finds an **electrical wall switch**,
confirms it across consecutive frames, and only then begins thermal analysis
inside a locked ROI. This detector is a classical computer-vision heuristic
(no trained weights yet): it looks for a plate-like rounded rectangle with a
distinct central rocker/toggle — the visual signature of a wall switch.

Honesty note: this is a *heuristic* detector. It reports a structural
similarity score, never a claim about what the switch is wired to. A properly
trained model can replace it later behind the same interface.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SwitchCandidate:
    """A detected wall-switch plate."""

    bbox: tuple[int, int, int, int]  # pixel (x1, y1, x2, y2)
    confidence: float  # structural similarity 0..1
    center: tuple[float, float]  # normalized (cx, cy) in 0..1
    size_fraction: float  # height / frame height

    def to_dict(self) -> dict:
        x1, y1, x2, y2 = self.bbox
        return {
            "bbox": [x1, y1, x2, y2],
            "bbox_norm": None,  # filled by the caller when frame shape known
            "confidence": round(self.confidence, 3),
            "center": [round(self.center[0], 3), round(self.center[1], 3)],
            "size_fraction": round(self.size_fraction, 3),
        }


class SwitchDetector:
    """Rule-based wall switch detector (plates with a central rocker)."""

    name = "cv-switch"

    def __init__(self, min_confidence: float = 0.55) -> None:
        self.min_confidence = min_confidence

    def detect(self, frame: np.ndarray) -> list[SwitchCandidate]:
        """Return wall-switch candidates, best first, above the confidence gate.

        The gate is deliberately strict: a candidate must look structurally
        like a switch plate (crisp rectangle, moderate internal structure, a
        darker central rocker) before it is even considered. Downstream the
        controller additionally requires N consecutive confirmed frames.
        """
        h, w = frame.shape[:2]
        if h < 24 or w < 24:
            return []

        # Fast lane: downscale wide frames (the detector is heuristic and only
        # needs plate proportions, not fine detail).
        scale = 1.0
        small = frame
        if w > 640:
            scale = 640.0 / w
            small = cv2.resize(frame, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        sh, sw = small.shape[:2]
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)

        # Candidates: bright plate-like regions (plates are usually lighter
        # than the wall) plus edge-closed rectangles for dark plates.
        candidates = self._bright_regions(gray)
        candidates += self._edge_rectangles(gray)
        # drop overlapping duplicates, keep best per region
        candidates = _dedupe(candidates)

        detections: list[SwitchCandidate] = []
        for (x, y, cw, ch, _contour_area) in candidates:
            # size gate: the switch should occupy a plausible share of the frame
            if cw < 24 or ch < 24:
                continue
            area = cw * ch
            if area < (sh * sw) * 0.005 or area > (sh * sw) * 0.35:
                continue
            if cw / max(ch, 1) > 3.0 or ch / max(cw, 1) > 3.0:  # plates are ~square-ish
                continue
            conf = _switch_similarity(gray, x, y, cw, ch)
            if conf < self.min_confidence:
                continue
            x1, y1 = int(x / scale), int(y / scale)
            x2, y2 = int((x + cw) / scale), int((y + ch) / scale)
            detections.append(
                SwitchCandidate(
                    bbox=(x1, y1, x2, y2),
                    confidence=float(conf),
                    center=((x1 + x2) / 2 / w, (y1 + y2) / 2 / h),
                    size_fraction=(y2 - y1) / h,
                )
            )
        detections.sort(key=lambda c: c.confidence, reverse=True)
        return detections[:3]

    # ------------------------------------------------------------------
    def _bright_regions(self, gray: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        """Light plate regions: brighter-than-surrounding rectangles."""
        h, w = gray.shape
        mean = float(gray.mean())
        _, thresh = cv2.threshold(gray, min(220, int(mean + 18)), 255, cv2.THRESH_BINARY)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        out: list[tuple[int, int, int, int, float]] = []
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
            x, y, cw, ch = cv2.boundingRect(cnt)
            if cw < 20 or ch < 20:
                continue
            out.append((x, y, cw, ch, float(cv2.contourArea(cnt))))
        return out

    def _edge_rectangles(self, gray: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        """Closed-edge rectangles (catches dark plates on light walls)."""
        edges = cv2.Canny(gray, 60, 160)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
        out: list[tuple[int, int, int, int, float]] = []
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:12]:
            x, y, cw, ch = cv2.boundingRect(cnt)
            if cw < 20 or ch < 20:
                continue
            # the rectangle must be largely closed (contour fills most of bbox)
            rect_area = cw * ch
            approx = cv2.approxPolyDP(cnt, 0.03 * cv2.arcLength(cnt, True), True)
            if cv2.contourArea(cnt) / rect_area < 0.45:
                continue
            out.append((x, y, cw, ch, float(cv2.contourArea(cnt)) if len(approx) in (4, 5) else float(rect_area * 0.5)))
        return out


def _switch_similarity(gray: np.ndarray, x: int, y: int, cw: int, ch: int) -> float:
    """Structural 0..1 score for a wall-switch plate candidate.

    Combines: plate fill (how much of the box differs from the surrounding
    wall), crisp border contrast, internal structure (a rocker/toggle makes
    the plate non-uniform — a distinct central band), and a center-rocker
    bias (the toggle sits in the middle). A uniform wall patch scores very
    low; a genuine switch scores high (≥ ~0.9 for a crisp plate).
    """
    h, w = gray.shape
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(w, x + cw), min(h, y + ch)
    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        return 0.0

    # surrounding-wall reference: median of a ring just outside the box
    ring_px = _wall_ring_pixels(gray, x1, y1, x2, y2)
    wall_ref = float(np.median(ring_px)) if ring_px.size else float(np.median(gray))
    # fill: share of the box that clearly differs from the wall (plate + rocker
    # both count — a switch is a *feature* on the wall, not a hole in a mask)
    diff = np.abs(roi.astype(np.float32) - wall_ref)
    fill = float(np.mean(diff >= 15.0))

    # border contrast: brightness jump across the perimeter
    edge_n = min(1.0, _border_crossing(gray, x1, y1, x2 - x1, y2 - y1) / 30.0)

    # internal structure: the plate should have moderate variance (rocker) —
    # not dead uniform like a wall patch, not chaotic like clutter.
    roi_std = float(roi.std())
    structure = 1.0 - abs(roi_std - 30.0) / 45.0
    structure = float(np.clip(structure, 0.0, 1.0))

    # central rocker: a horizontal band through the middle should differ from
    # the plate's top/bottom strips (the toggle bar).
    band = max(2, ch // 5)
    mid = roi[ch // 2 - band // 2 : ch // 2 + band // 2, :]
    top = roi[:band, :]
    bot = roi[ch - band :, :]
    rocker_diff = float(np.abs(mid.mean() - (top.mean() + bot.mean()) / 2))
    rocker_n = min(1.0, rocker_diff / 25.0)

    score = 0.32 * fill + 0.22 * edge_n + 0.18 * structure + 0.28 * rocker_n
    return float(np.clip(score, 0.0, 1.0))


def _wall_ring_pixels(gray: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
    """Brightness samples from a ring just outside the candidate box."""
    h, w = gray.shape
    pad = max(3, int((x2 - x1) * 0.08))
    rx1, ry1 = max(0, x1 - pad), max(0, y1 - pad)
    rx2, ry2 = min(w, x2 + pad), min(h, y2 + pad)
    outer = np.zeros_like(gray, dtype=bool)
    outer[ry1:ry2, rx1:rx2] = True
    box = np.zeros_like(gray, dtype=bool)
    box[y1:y2, x1:x2] = True
    return gray[outer & ~box]


def _border_crossing(gray: np.ndarray, x: int, y: int, cw: int, ch: int) -> float:
    """Mean brightness jump across the candidate's perimeter (crisp edge?)."""
    h, w = gray.shape
    diffs: list[float] = []
    for i in range(x, min(w, x + cw)):
        if y - 1 >= 0:
            diffs.append(abs(int(gray[y - 1, i]) - int(gray[y, i])))
        if y + ch < h:
            diffs.append(abs(int(gray[y + ch, i]) - int(gray[y + ch - 1, i])))
    for j in range(y, min(h, y + ch)):
        if x - 1 >= 0:
            diffs.append(abs(int(gray[j, x - 1]) - int(gray[j, x])))
        if x + cw < w:
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


def _dedupe(candidates: list[tuple[int, int, int, int, float]]) -> list[tuple[int, int, int, int, float]]:
    """Remove overlapping duplicates, keeping the one with the larger area."""
    ordered = sorted(candidates, key=lambda c: c[2] * c[3], reverse=True)
    kept: list[tuple[int, int, int, int, float]] = []
    for cand in ordered:
        x, y, cw, ch, area = cand
        box = (x, y, x + cw, y + ch)
        if any(_iou(box, (kx, ky, kx + kw, ky + kh)) > 0.35 for kx, ky, kw, kh, _ in kept):
            continue
        kept.append(cand)
    return kept
