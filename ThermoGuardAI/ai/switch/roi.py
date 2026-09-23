"""Inspection ROI around the confirmed switch (S1).

Once the switch is confirmed, the switch bounding box is expanded into an
inspection region that also covers the immediate wall area, nearby visible
wiring and the relevant surrounding thermal area. The expansion factor is
configurable (settings.switch_roi_padding), not hardcoded in the analysis.
"""
from __future__ import annotations

import numpy as np


def expand_roi(
    bbox: tuple[int, int, int, int],
    frame_shape: tuple[int, int],
    padding: float = 0.35,
) -> tuple[int, int, int, int]:
    """Expand a switch bbox into an inspection ROI (pixel (x1, y1, x2, y2)).

    `padding` is the expansion fraction of the box's own width/height in every
    direction. The ROI is clamped to the frame.
    """
    h, w = frame_shape[:2]
    x1, y1, x2, y2 = bbox
    bw, bh = x2 - x1, y2 - y1
    pad_x = max(8, int(bw * padding))
    pad_y = max(8, int(bh * padding))
    roi = (
        max(0, x1 - pad_x),
        max(0, y1 - pad_y),
        min(w, x2 + pad_x),
        min(h, y2 + pad_y),
    )
    return roi


def roi_to_norm(roi: tuple[int, int, int, int], frame_shape: tuple[int, int]) -> tuple[float, float, float, float]:
    """Normalized (x, y, w, h) in 0..1 for a pixel ROI."""
    h, w = frame_shape[:2]
    x1, y1, x2, y2 = roi
    return (x1 / w, y1 / h, max(0.0, (x2 - x1) / w), max(0.0, (y2 - y1) / h))


def bbox_to_norm(bbox: tuple[int, int, int, int], frame_shape: tuple[int, int]) -> tuple[float, float, float, float]:
    """Normalized (x, y, w, h) in 0..1 for a pixel bbox."""
    h, w = frame_shape[:2]
    x1, y1, x2, y2 = bbox
    return (x1 / w, y1 / h, max(0.0, (x2 - x1) / w), max(0.0, (y2 - y1) / h))


def wall_ring_norm(roi: tuple[int, int, int, int], frame_shape: tuple[int, int]) -> tuple[float, float, float, float]:
    """Normalized wall ring (outer reference) just beyond the ROI.

    The ring spans from the ROI edge outward by the ROI's own size — the
    "nearby wall" reference the switch temperature is compared against.
    """
    h, w = frame_shape[:2]
    x1, y1, x2, y2 = roi
    bw, bh = x2 - x1, y2 - y1
    ring = (
        max(0, int(x1 - bw * 0.5)),
        max(0, int(y1 - bh * 0.5)),
        min(w, int(x2 + bw * 0.5)),
        min(h, int(y2 + bh * 0.5)),
    )
    return roi_to_norm(ring, frame_shape)


def numpy_clamp_roi(roi: tuple[int, int, int, int], shape: tuple[int, int]) -> tuple[int, int, int, int]:
    """Guard for direct array slicing: clamp and normalize indices."""
    h, w = shape[:2]
    x1, y1, x2, y2 = roi
    x1, y1 = max(0, min(int(x1), w - 1)), max(0, min(int(y1), h - 1))
    x2 = max(x1 + 1, min(int(x2), w))
    y2 = max(y1 + 1, min(int(y2), h))
    return (x1, y1, x2, y2)


def sample_regions(
    temps: np.ndarray,
    roi: tuple[int, int, int, int],
    frame_shape: tuple[int, int],
) -> dict[str, np.ndarray]:
    """Slice the thermal array into switch-ROI, wall-ring and outer regions.

    `temps` is the full-frame temperature map (already resized to frame size).
    """
    x1, y1, x2, y2 = numpy_clamp_roi(roi, frame_shape)
    roi_arr = temps[y1:y2, x1:x2]
    # wall ring: an ANNULUS around the ROI (up to ROI size beyond it) that
    # EXCLUDES the ROI itself — the switch's own heat must never inflate the
    # surrounding-wall reference it is compared against.
    h, w = frame_shape[:2]
    bw, bh = x2 - x1, y2 - y1
    wx1, wy1 = max(0, x1 - bw // 2), max(0, y1 - bh // 2)
    wx2, wy2 = min(w, x2 + bw // 2), min(h, y2 + bh // 2)
    box_mask = np.zeros_like(temps, dtype=bool)
    box_mask[y1:y2, x1:x2] = True
    ring_mask = np.zeros_like(temps, dtype=bool)
    ring_mask[wy1:wy2, wx1:wx2] = True
    wall = temps[ring_mask & ~box_mask]
    return {"roi": roi_arr, "wall": wall}
