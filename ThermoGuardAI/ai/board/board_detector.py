"""Single-class switchboard detector — any type, any size.

Why this exists
---------------
The previous engine detected 17 component classes (circuit_breaker, fuse,
relay, busbar, terminal, …) and a separate ``SwitchDetector`` tuned for a small
*wall switch plate*. Both are wrong for this product. We now detect exactly one
thing: the **switch board** — consumer unit, distribution board, panelboard,
load centre, MCB box, industrial switchgear cabinet, or a plain switch plate —
and we accept it at any size in the frame, including filling the frame edge to
edge when the user holds the phone close.

Honesty note
------------
This is a *heuristic* detector with no trained weights. It reports a structural
similarity score describing how board-like a region looks. It never claims to
know what the board is wired to, its rating, or its standard compliance.
A trained model can replace it later behind the same ``detect()`` interface.

What makes a switch board look like a switch board
--------------------------------------------------
1. **Planar quadrilateral** — a flat faceplate/door with a crisp border, often
   seen at a slight perspective angle.
2. **Rectilinearity** — its internal edges are overwhelmingly horizontal or
   vertical (breaker rows, plate seams, DIN rails). Clutter is not.
3. **Repetition** — rows or columns of near-identical breaker toggles produce a
   periodic edge projection. This is the single strongest discriminator between
   a distribution board and any other rectangle on a wall.
4. **Plate uniformity** — the body is a dominant low-saturation colour (grey,
   white, beige, metal) rather than a rainbow of hues.
5. **Structure density** — busy enough to hold devices, not chaotic like a
   bookshelf or a window with a view.

A blank wall scores near zero (no structure, no repetition). A door or picture
frame scores low (border but no internal repetition). A real board scores high.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# The detector works on a downscaled copy — board geometry survives it and the
# heuristics get ~6x faster, which matters when frames stream from a phone.
_WORK_WIDTH = 640

# Size gates. Deliberately permissive: the product promise is "any size".
# There is deliberately NO upper area gate — a board photographed close up
# legitimately fills the entire frame, and an upper bound would reject exactly
# the framing users naturally choose.
_MIN_AREA_FRAC = 0.02  # a small board across the room
_MIN_ASPECT = 0.18  # very tall panelboard / riser
_MAX_ASPECT = 5.5  # very wide switchgear lineup

# Fraction of the region trimmed off each side before measuring INTERNAL cues.
# Without this the region's own outline supplies the axis-aligned edges and any
# plain rectangle (a door, a cupboard, a framed picture) scores like a board.
_INTERIOR_INSET = 0.08


@dataclass
class BoardCandidate:
    """A detected switch board region."""

    bbox: tuple[int, int, int, int]  # pixel (x1, y1, x2, y2) in the ORIGINAL frame
    confidence: float  # 0..1 structural similarity
    center: tuple[float, float]  # normalized (cx, cy)
    area_fraction: float  # bbox area / frame area
    quad: list[tuple[int, int]] | None = None  # 4 corners when a clean quad was found
    fills_frame: bool = False  # board extends past the frame edge
    scores: dict[str, float] = field(default_factory=dict)  # per-cue breakdown

    def to_dict(self) -> dict:
        x1, y1, x2, y2 = self.bbox
        return {
            "bbox": [x1, y1, x2, y2],
            "confidence": round(self.confidence, 3),
            "center": [round(self.center[0], 3), round(self.center[1], 3)],
            "area_fraction": round(self.area_fraction, 3),
            "quad": [[int(px), int(py)] for px, py in self.quad] if self.quad else None,
            "fills_frame": self.fills_frame,
            "scores": {k: round(v, 3) for k, v in self.scores.items()},
        }


class BoardDetector:
    """Rule-based switch board detector.

    ``min_confidence`` gates what is returned. The session controller
    additionally requires the board to hold still for N consecutive frames
    before it starts analysing, so a brief false positive cannot start a scan.

    Calibration against synthetic references (boards at 25 % / 43 % / 100 % of
    frame, tall 4x3 board, blank wall, colourful clutter, panelled door):

        boards      0.62 - 0.79
        clutter     0.49
        door        0.11
        blank wall  0.03

    The default gate sits at 0.50, just above clutter. This is a heuristic used
    as *guidance*: the session never blocks a user on it — they can always scan
    the full frame or place the region by hand.
    """

    name = "cv-switchboard"
    label = "switchboard"

    def __init__(self, min_confidence: float = 0.50) -> None:
        self.min_confidence = min_confidence

    # ------------------------------------------------------------------
    def detect(self, frame: np.ndarray) -> list[BoardCandidate]:
        """Return switch board candidates, best first, above the gate."""
        if frame is None or frame.size == 0:
            return []
        h, w = frame.shape[:2]
        if h < 32 or w < 32:
            return []

        scale = 1.0
        small = frame
        if w > _WORK_WIDTH:
            scale = _WORK_WIDTH / float(w)
            small = cv2.resize(frame, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        sh, sw = small.shape[:2]
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.bilateralFilter(gray, 5, 40, 40)  # keep edges, kill sensor noise
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        edges = cv2.Canny(gray, 45, 140)

        boxes = self._quad_candidates(gray)
        boxes += self._plate_candidates(hsv)
        boxes += self._structure_candidates(edges)
        boxes.append(_FULL_FRAME_BOX(sw, sh))  # board may exceed the frame
        boxes = _dedupe(boxes)

        out: list[BoardCandidate] = []
        for box in boxes:
            x, y, bw, bh, quad = box
            if bw < 28 or bh < 28:
                continue
            area_frac = (bw * bh) / float(sw * sh)
            if area_frac < _MIN_AREA_FRAC:
                continue
            aspect = bw / float(max(bh, 1))
            if aspect < _MIN_ASPECT or aspect > _MAX_ASPECT:
                continue

            conf, parts = _board_score(gray, hsv, edges, x, y, bw, bh)
            if conf < self.min_confidence:
                continue

            # map back to original frame coordinates
            x1, y1 = int(x / scale), int(y / scale)
            x2, y2 = int((x + bw) / scale), int((y + bh) / scale)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            touches = x <= 1 or y <= 1 or (x + bw) >= sw - 1 or (y + bh) >= sh - 1
            out.append(
                BoardCandidate(
                    bbox=(x1, y1, x2, y2),
                    confidence=float(conf),
                    center=(((x1 + x2) / 2) / w, ((y1 + y2) / 2) / h),
                    area_fraction=float((x2 - x1) * (y2 - y1)) / float(w * h),
                    quad=[(int(px / scale), int(py / scale)) for px, py in quad] if quad else None,
                    fills_frame=bool(touches and area_frac > 0.55),
                    scores=parts,
                )
            )

        out.sort(key=lambda c: c.confidence, reverse=True)
        return out[:3]

    # ------------------------------------------------------------------
    def _quad_candidates(self, gray: np.ndarray) -> list[tuple]:
        """Closed quadrilaterals — the faceplate/door outline, perspective ok."""
        out: list[tuple] = []
        # Two edge scales: a soft pass for low-contrast metal-on-white, and a
        # hard pass for crisp painted enclosures.
        for lo, hi in ((30, 90), (60, 180)):
            edges = cv2.Canny(gray, lo, hi)
            edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
            contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:14]:
                peri = cv2.arcLength(cnt, True)
                if peri < 120:
                    continue
                approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
                x, y, bw, bh = cv2.boundingRect(approx)
                if bw < 28 or bh < 28:
                    continue
                # the contour should actually fill its bounding box (a real
                # plate), not be a thin squiggle that happens to span it
                if cv2.contourArea(cnt) / float(bw * bh) < 0.40:
                    continue
                quad = None
                if len(approx) == 4 and cv2.isContourConvex(approx):
                    quad = [(int(p[0][0]), int(p[0][1])) for p in approx]
                out.append((x, y, bw, bh, quad))
        return out

    def _plate_candidates(self, hsv: np.ndarray) -> list[tuple]:
        """Large low-saturation regions — grey/white/beige/metal enclosures."""
        sat, val = hsv[:, :, 1], hsv[:, :, 2]
        # boards are desaturated and mid-to-bright; exclude deep shadow
        mask = ((sat < 70) & (val > 55)).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        out: list[tuple] = []
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
            x, y, bw, bh = cv2.boundingRect(cnt)
            if bw >= 28 and bh >= 28:
                out.append((x, y, bw, bh, None))
        return out

    def _structure_candidates(self, edges: np.ndarray) -> list[tuple]:
        """Dense rectilinear-structure blocks — breaker banks and DIN rails.

        Works even when the enclosure border is out of frame, which is the
        common case for a close-up phone shot of a consumer unit.
        """
        h, w = edges.shape
        cell = 16
        gh, gw = max(1, h // cell), max(1, w // cell)
        # rectilinear edge energy per cell
        grid = np.zeros((gh, gw), np.float32)
        for j in range(gh):
            for i in range(gw):
                blk = edges[j * cell : (j + 1) * cell, i * cell : (i + 1) * cell]
                grid[j, i] = float(np.count_nonzero(blk)) / max(1, blk.size)
        hot = (grid > max(0.06, float(grid.mean()) * 1.25)).astype(np.uint8)
        hot = cv2.morphologyEx(hot, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(hot, 8)
        out: list[tuple] = []
        for idx in range(1, n):
            cx, cy, cw, ch, area = stats[idx]
            if area < 4:
                continue
            x, y = cx * cell, cy * cell
            bw, bh = cw * cell, ch * cell
            if bw >= 28 and bh >= 28:
                out.append((x, y, min(bw, w - x), min(bh, h - y), None))
        out.sort(key=lambda b: b[2] * b[3], reverse=True)
        return out[:6]


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------
def _board_score(
    gray: np.ndarray,
    hsv: np.ndarray,
    edges: np.ndarray,
    x: int,
    y: int,
    bw: int,
    bh: int,
) -> tuple[float, dict[str, float]]:
    """Structural 0..1 score that a region is a switch board, plus a breakdown.

    Cues are combined with fixed weights. When the region touches the frame
    edge the border cue is unmeasurable on those sides, so its weight is
    redistributed onto the internal cues rather than scoring the board down for
    being photographed close up.

    Critically, every *internal* cue is measured on the inset interior. The
    region's own outline is excluded, because otherwise a plain rectangle
    supplies perfect axis-aligned edges and scores like a populated board.
    """
    h, w = gray.shape
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(w, x + bw), min(h, y + bh)
    roi = gray[y1:y2, x1:x2]
    if roi.size == 0 or roi.shape[0] < 12 or roi.shape[1] < 12:
        return 0.0, {}

    # Interior window — the board's face without its frame.
    ins_x = max(2, int((x2 - x1) * _INTERIOR_INSET))
    ins_y = max(2, int((y2 - y1) * _INTERIOR_INSET))
    ix1, iy1 = min(x2 - 4, x1 + ins_x), min(y2 - 4, y1 + ins_y)
    ix2, iy2 = max(ix1 + 4, x2 - ins_x), max(iy1 + 4, y2 - ins_y)
    interior = gray[iy1:iy2, ix1:ix2]
    interior_edges = edges[iy1:iy2, ix1:ix2]

    rect = _rectilinearity(interior)
    rep = _device_repetition(interior_edges)
    dev = _device_blobs(interior)
    struct = _structure_density(interior_edges)
    uni = _plate_uniformity(hsv[y1:y2, x1:x2])

    interior_sides = _interior_sides(x1, y1, x2, y2, w, h)
    border = _border_contrast(gray, x1, y1, x2, y2, interior_sides)

    weights = {"rect": 0.18, "rep": 0.24, "dev": 0.22, "struct": 0.12, "uni": 0.08, "border": 0.16}
    if not interior_sides:  # board exceeds the frame on every side
        spare = weights.pop("border")
        border = 0.0
        total = sum(weights.values())
        weights = {k: v + v / total * spare for k, v in weights.items()}
    elif len(interior_sides) < 4:
        # partial border information — trust it proportionally
        weights["border"] *= len(interior_sides) / 4.0

    parts = {"rect": rect, "rep": rep, "dev": dev, "struct": struct, "uni": uni, "border": border}
    score = sum(weights.get(k, 0.0) * v for k, v in parts.items())
    score /= max(1e-6, sum(weights.values()))

    # A switch board carries *devices*: repeated breaker/switch bodies on its
    # face. Without that evidence the region is some other flat rectangle — a
    # door, a cupboard, a blank wall panel — and is heavily demoted. This single
    # gate is what separates a board from any rectangle.
    if struct < 0.03 or max(rep, dev) < 0.12:
        score *= 0.30
    elif rect < 0.20:
        score *= 0.55

    # Board faces are a dominant desaturated tone — grey, white, beige, or bare
    # metal. Colourful clutter (shelves, tools, packaging) can accidentally
    # produce device-like blobs, so low plate uniformity demotes the region.
    if uni < 0.62:
        score *= 0.55

    parts["weighted"] = float(np.clip(score, 0.0, 1.0))
    return float(np.clip(score, 0.0, 1.0)), parts


def _rectilinearity(roi: np.ndarray) -> float:
    """Share of gradient energy aligned to horizontal or vertical axes.

    Breaker rows, DIN rails and plate seams are axis-aligned. Foliage, faces,
    crumpled fabric and general clutter are not.
    """
    gx = cv2.Sobel(roi, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(roi, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    strong = mag > max(12.0, float(np.percentile(mag, 80)))
    if int(np.count_nonzero(strong)) < 24:
        return 0.0
    ang = np.abs(np.degrees(np.arctan2(gy[strong], gx[strong]))) % 180.0
    axis = (ang < 22.0) | (ang > 158.0) | ((ang > 68.0) & (ang < 112.0))
    return float(np.count_nonzero(axis)) / float(axis.size)


def _device_repetition(roi_edges: np.ndarray) -> float:
    """Periodicity of the interior edge projections — rows/columns of breakers.

    Projects edge pixels onto each axis and measures how strongly the profile
    repeats, via the best peak of its normalised autocorrelation.

    The period band matters more than the peak height. A bank of breakers
    repeats *many* times across the board (short period, 5-25 repeats); a
    panelled door repeats two or three times (long period). Only lags inside the
    fine-pitch band count, and the score scales with how many repeats fit — so
    coarse panelling cannot impersonate a breaker row.
    """
    if roi_edges.size == 0:
        return 0.0
    best = 0.0
    for proj in (roi_edges.sum(axis=0, dtype=np.float32), roi_edges.sum(axis=1, dtype=np.float32)):
        n = proj.size
        if n < 32:
            continue
        sig = proj - proj.mean()
        denom = float(np.dot(sig, sig))
        if denom <= 1e-6:
            continue
        ac = np.correlate(sig, sig, mode="full")[n - 1 :] / denom
        lo = max(3, int(n * 0.04))  # >= ~25 repeats
        hi = max(lo + 2, int(n * 0.24))  # <= ~4 repeats
        if hi >= ac.size:
            hi = ac.size - 1
        if hi <= lo:
            continue
        window = ac[lo:hi]
        lag = int(np.argmax(window)) + lo
        peak = float(window.max())
        repeats = n / float(max(1, lag))
        best = max(best, peak * float(np.clip(repeats / 5.0, 0.0, 1.0)))
    return float(np.clip(best, 0.0, 1.0))


def _device_blobs(interior: np.ndarray) -> float:
    """Count of similar-sized small elements on the board face.

    Breakers, switches, fuse carriers and indicator lamps read as a population
    of small blobs with consistent size. A door has a handful of large panels; a
    blank plate has none. Scored on both how many were found and how uniform
    their areas are, since a board's devices are near-identical by construction.
    """
    if interior.size < 400:
        return 0.0
    ih, iw = interior.shape[:2]
    total = float(ih * iw)
    blur = cv2.GaussianBlur(interior, (3, 3), 0)
    # Adaptive threshold isolates device bodies from the plate regardless of the
    # overall exposure, which varies wildly between phone cameras.
    block = max(11, (min(ih, iw) // 8) | 1)
    areas: list[float] = []
    for invert in (cv2.THRESH_BINARY, cv2.THRESH_BINARY_INV):
        mask = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C, invert, block, 6)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, _labels, stats, _c = cv2.connectedComponentsWithStats(mask, 8)
        for idx in range(1, n):
            bx, by, bwid, bhei, area = stats[idx]
            frac = area / total
            if frac < 0.0004 or frac > 0.05:  # not a device-sized element
                continue
            aspect = bwid / float(max(1, bhei))
            if aspect < 0.12 or aspect > 8.0:
                continue
            if area / float(max(1, bwid * bhei)) < 0.35:  # squiggle, not a body
                continue
            areas.append(float(area))
    if len(areas) < 3:
        return 0.0
    arr = np.asarray(areas, dtype=np.float32)
    count_score = float(np.clip(len(areas) / 10.0, 0.0, 1.0))
    # coefficient of variation → 0 when all devices are the same size
    cv_ratio = float(np.std(arr) / max(1.0, float(np.mean(arr))))
    consistency = float(np.clip(1.0 - cv_ratio / 1.1, 0.0, 1.0))
    return float(np.clip(0.62 * count_score + 0.38 * consistency, 0.0, 1.0))


def _structure_density(roi_edges: np.ndarray) -> float:
    """Edge density mapped to a 'busy enough, not chaotic' band.

    Peaks around ~12% edge pixels, which is typical of a populated board, and
    falls off for blank plates (too sparse) and visual clutter (too dense).
    """
    if roi_edges.size == 0:
        return 0.0
    d = float(np.count_nonzero(roi_edges)) / float(roi_edges.size)
    if d <= 0.0:
        return 0.0
    ideal, width = 0.12, 0.16
    return float(np.clip(1.0 - abs(d - ideal) / width, 0.0, 1.0))


def _plate_uniformity(roi_hsv: np.ndarray) -> float:
    """How much of the region belongs to one dominant desaturated colour."""
    if roi_hsv.size == 0:
        return 0.0
    sat = roi_hsv[:, :, 1].astype(np.float32)
    val = roi_hsv[:, :, 2].astype(np.float32)
    desat = float(np.mean(sat < 80.0))
    # dominant brightness band: a plate is mostly one tone
    hist = cv2.calcHist([roi_hsv[:, :, 2]], [0], None, [16], [0, 256]).ravel()
    dominant = float(hist.max() / max(1.0, hist.sum()))
    lit = float(np.mean(val > 45.0))
    return float(np.clip(0.45 * desat + 0.35 * dominant * 2.2 + 0.20 * lit, 0.0, 1.0))


def _interior_sides(x1: int, y1: int, x2: int, y2: int, w: int, h: int) -> list[str]:
    """Which sides of the box are genuinely inside the frame (border measurable)."""
    sides = []
    if x1 > 2:
        sides.append("left")
    if y1 > 2:
        sides.append("top")
    if x2 < w - 2:
        sides.append("right")
    if y2 < h - 2:
        sides.append("bottom")
    return sides


def _border_contrast(gray: np.ndarray, x1: int, y1: int, x2: int, y2: int, sides: list[str]) -> float:
    """Mean brightness step across the measurable sides of the perimeter."""
    if not sides:
        return 0.0
    pad = 3
    diffs: list[float] = []
    inner = gray[y1:y2, x1:x2]
    if inner.size == 0:
        return 0.0
    inner_mean = float(inner.mean())
    h, w = gray.shape
    if "top" in sides:
        strip = gray[max(0, y1 - pad) : y1, x1:x2]
        if strip.size:
            diffs.append(abs(float(strip.mean()) - inner_mean))
    if "bottom" in sides:
        strip = gray[y2 : min(h, y2 + pad), x1:x2]
        if strip.size:
            diffs.append(abs(float(strip.mean()) - inner_mean))
    if "left" in sides:
        strip = gray[y1:y2, max(0, x1 - pad) : x1]
        if strip.size:
            diffs.append(abs(float(strip.mean()) - inner_mean))
    if "right" in sides:
        strip = gray[y1:y2, x2 : min(w, x2 + pad)]
        if strip.size:
            diffs.append(abs(float(strip.mean()) - inner_mean))
    if not diffs:
        return 0.0
    return float(np.clip(float(np.mean(diffs)) / 28.0, 0.0, 1.0))


# ----------------------------------------------------------------------
# Candidate bookkeeping
# ----------------------------------------------------------------------
def _FULL_FRAME_BOX(sw: int, sh: int) -> tuple:  # noqa: N802 — reads as a constant at the call site
    """A whole-frame candidate, for boards held close enough to overflow it."""
    m = 2
    return (m, m, sw - 2 * m, sh - 2 * m, None)


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def _dedupe(boxes: list[tuple]) -> list[tuple]:
    """Drop heavily overlapping duplicates, preferring larger boxes with quads."""
    ordered = sorted(boxes, key=lambda b: (b[2] * b[3], b[4] is not None), reverse=True)
    kept: list[tuple] = []
    for box in ordered:
        x, y, bw, bh, _quad = box
        rect = (x, y, x + bw, y + bh)
        if any(_iou(rect, (kx, ky, kx + kw, ky + kh)) > 0.55 for kx, ky, kw, kh, _ in kept):
            continue
        kept.append(box)
    return kept
