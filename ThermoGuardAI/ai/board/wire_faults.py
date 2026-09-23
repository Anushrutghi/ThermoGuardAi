"""Wiring and board fault heuristics — conservative by design.

Why this replaces ``ai/visual/fault_heuristics.py`` for the board path
---------------------------------------------------------------------
The old module produced a **CRITICAL "smoke_haze" finding on a clean board**.
Its mask was::

    grayish = (sat < 40) & (val > 60) & (val < 210)

That matches *any* mid-grey surface — and a switch board is mid-grey. So the
single most alarming verdict the tool could give fired on healthy hardware.

For an electrical-safety tool, a false CRITICAL and a false "all clear" are
both dangerous: one sends people to needless expense and teaches them to ignore
the tool, the other leaves a real hazard energised. So every check here must
clear three hurdles before it will speak:

1. **A positive signal, not an absence.** Smoke is detected as a *plume with
   directional structure*, never as "this pixel is greyish".
2. **Local contrast against the board's own surface**, not an absolute colour
   threshold. Boards range from white to dark grey to bare metal, and phone
   white balance shifts everything.
3. **A plausible size and shape.** Faults are localised. Anything spanning most
   of the frame is lighting, background or the board itself.

Severity is deliberately capped: no RGB heuristic may return CRITICAL on its
own. CRITICAL requires corroboration — heat evidence in the same region — which
the session layer decides, because a photo alone cannot establish it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

from ai.detector.base import HealthStatus

logger = logging.getLogger(__name__)

_ORDER = {HealthStatus.HEALTHY: 0, HealthStatus.WARNING: 1, HealthStatus.HIGH_RISK: 2, HealthStatus.CRITICAL: 3}


@dataclass
class BoardFault:
    """One suspected fault on the board.

    ``plain`` is the whole point of this dataclass: a sentence the person
    holding the phone can act on, with no electrical vocabulary.
    """

    kind: str  # machine key, e.g. "scorching"
    title: str  # short human title, e.g. "Burn marks"
    severity: HealthStatus
    confidence: float  # 0..1
    bbox: tuple[int, int, int, int]  # pixel box in the ORIGINAL frame
    plain: str  # what a non-technical person needs to know
    evidence: str  # why the tool thinks so (auditable)

    def to_dict(self) -> dict:
        x1, y1, x2, y2 = self.bbox
        return {
            "kind": self.kind,
            "title": self.title,
            "severity": self.severity.value if hasattr(self.severity, "value") else str(self.severity),
            "confidence": round(self.confidence, 3),
            "bbox": [int(x1), int(y1), int(x2), int(y2)],
            "plain": self.plain,
            "evidence": self.evidence,
        }


@dataclass
class BoardFaultResult:
    faults: list[BoardFault] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)
    image_quality: str = "ok"  # ok | dark | blurry | glare
    quality_note: str = ""

    @property
    def worst(self) -> HealthStatus:
        return max((f.severity for f in self.faults), key=lambda s: _ORDER[s], default=HealthStatus.HEALTHY)

    def to_dict(self) -> dict:
        return {
            "faults": [f.to_dict() for f in self.faults],
            "checks_run": list(self.checks_run),
            "image_quality": self.image_quality,
            "quality_note": self.quality_note,
            "worst_severity": self.worst.value if hasattr(self.worst, "value") else str(self.worst),
        }


class BoardFaultDetector:
    """Runs the conservative fault checks over the board region."""

    def analyze(self, frame: np.ndarray, board_bbox: tuple[int, int, int, int] | None = None) -> BoardFaultResult:
        if frame is None or frame.size == 0:
            return BoardFaultResult(image_quality="dark", quality_note="No image received.")

        h, w = frame.shape[:2]
        x1, y1, x2, y2 = board_bbox if board_bbox else (0, 0, w, h)
        x1, y1 = max(0, min(x1, w - 2)), max(0, min(y1, h - 2))
        x2, y2 = max(x1 + 2, min(x2, w)), max(y1 + 2, min(y2, h))
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return BoardFaultResult(image_quality="dark", quality_note="Board region was empty.")

        quality, note = _image_quality(roi)
        result = BoardFaultResult(image_quality=quality, quality_note=note)

        # A photo too dark or too blurry to judge must say so rather than
        # inventing findings from noise — or worse, reporting "nothing found",
        # which reads as "safe".
        if quality in ("dark", "blurry"):
            result.quality_note = note
            return result

        checks = (
            ("scorching", _detect_scorching),
            ("smoke_plume", _detect_smoke_plume),
            ("arc_damage", _detect_arc_damage),
            ("exposed_conductor", _detect_exposed_conductor),
            ("loose_wire", _detect_loose_wire),
            ("moisture", _detect_moisture),
            ("open_slot", _detect_open_slot),
        )
        for name, fn in checks:
            result.checks_run.append(name)
            try:
                for fault in fn(roi):
                    # shift ROI-local boxes back into original frame space
                    fx1, fy1, fx2, fy2 = fault.bbox
                    fault.bbox = (fx1 + x1, fy1 + y1, fx2 + x1, fy2 + y1)
                    result.faults.append(fault)
            except Exception:  # a broken check must never abort the inspection
                logger.exception("Board fault check %s failed", name)

        result.faults.sort(key=lambda f: (_ORDER[f.severity], f.confidence), reverse=True)
        return result


# ----------------------------------------------------------------------
# Image quality — the gate that stops noise becoming findings
# ----------------------------------------------------------------------
def _image_quality(roi: np.ndarray) -> tuple[str, str]:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mean = float(gray.mean())
    if mean < 38.0:
        return "dark", "The photo is too dark to inspect. Turn on a light and try again."
    # Variance of Laplacian: the standard sharpness proxy. Below ~28 the fine
    # detail that every check depends on simply is not present.
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if sharp < 28.0:
        return "blurry", "The photo is blurry. Hold the phone steady and try again."
    if float(np.mean(gray > 248)) > 0.14:
        return "glare", "Strong glare on part of the board — some areas could not be checked."
    return "ok", ""


def _surface_stats(roi: np.ndarray) -> tuple[float, float]:
    """The board's own healthy brightness and spread, as the local reference.

    Every check compares against this rather than a fixed constant, so a white
    consumer unit and a dark industrial cabinet are both judged on their own
    terms — and so is a phone that white-balances the whole scene warm.
    """
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.percentile(gray, 60)), float(gray.std() + 1e-3)


def _boxes(mask: np.ndarray, min_frac: float, max_frac: float, min_fill: float = 0.30) -> list[tuple[int, int, int, int, float]]:
    """Connected regions of a mask within a plausible size band.

    Returns (x1, y1, x2, y2, area_fraction). ``min_fill`` rejects thin
    scattered squiggles that merely span a large bounding box.
    """
    total = float(mask.shape[0] * mask.shape[1])
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, _labels, stats, _c = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    out: list[tuple[int, int, int, int, float]] = []
    for idx in range(1, n):
        bx, by, bw, bh, area = stats[idx]
        frac = area / total
        if frac < min_frac or frac > max_frac:
            continue
        if area / float(max(1, bw * bh)) < min_fill:
            continue
        out.append((int(bx), int(by), int(bx + bw), int(by + bh), float(frac)))
    out.sort(key=lambda b: b[4], reverse=True)
    return out[:4]


# ----------------------------------------------------------------------
# The checks
# ----------------------------------------------------------------------
def _detect_scorching(roi: np.ndarray) -> list[BoardFault]:
    """Burn / scorch marks: dark AND brown-tinted AND flat, against the board.

    Requiring all three is what separates a burn from a shadow, a black
    breaker body, or a dark cable. Shadows keep the underlying texture; painted
    black plastic is neutral, not brown; burns are brown-tinted and matte.
    """
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.float32) * 2.0
    sat = hsv[:, :, 1].astype(np.float32)
    val = hsv[:, :, 2].astype(np.float32)
    ref, spread = _surface_stats(roi)

    much_darker = val < max(30.0, ref - 2.0 * spread)
    brown = (hue > 8.0) & (hue < 48.0) & (sat > 45.0)
    gray_img = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
    local_var = cv2.blur((gray_img - cv2.blur(gray_img, (9, 9))) ** 2, (9, 9))
    matte = local_var < 90.0

    mask = (much_darker & brown & matte).astype(np.uint8) * 255
    out: list[BoardFault] = []
    for bx1, by1, bx2, by2, frac in _boxes(mask, 0.0015, 0.16):
        conf = float(np.clip(0.45 + frac * 3.2, 0.0, 0.85))
        out.append(
            BoardFault(
                kind="scorching",
                title="Burn or scorch marks",
                severity=HealthStatus.HIGH_RISK,
                confidence=conf,
                bbox=(bx1, by1, bx2, by2),
                plain=(
                    "There is a dark brown mark that looks like burning. "
                    "Something here has been getting far too hot. Do not use this circuit "
                    "and have an electrician look at it."
                ),
                evidence=(
                    f"Region is {frac * 100:.1f}% of the board, much darker than the board surface, "
                    "brown-tinted and matte — consistent with scorching rather than shadow."
                ),
            )
        )
    return out


def _detect_smoke_plume(roi: np.ndarray) -> list[BoardFault]:
    """Smoke staining — a plume, detected by its directional structure.

    THIS IS THE REWRITTEN CHECK. The old one flagged any grey pixel. Smoke
    deposits above a vent or gap and fades upward, so the honest signal is a
    sustained vertical brightness gradient inside a localised column, not a
    colour. A uniformly grey board has no gradient and produces nothing.
    """
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray = cv2.GaussianBlur(gray, (0, 0), 2.0)
    ref, spread = _surface_stats(roi)

    # upward-fading darkening: brightness increases as you move up out of the stain
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=5)
    fade = cv2.blur(gy, (21, 21))
    darker = gray < (ref - 1.2 * spread)
    # a real plume is smooth; texture here means we are looking at hardware
    local_var = cv2.blur((gray - cv2.blur(gray, (11, 11))) ** 2, (11, 11))
    smooth = local_var < 55.0

    mask = ((fade > 1.6) & darker & smooth).astype(np.uint8) * 255
    out: list[BoardFault] = []
    for bx1, by1, bx2, by2, frac in _boxes(mask, 0.004, 0.13, min_fill=0.34):
        bh, bw = by2 - by1, bx2 - bx1
        # plumes are taller than wide; a wide band is shading, not smoke
        if bh < bw * 0.85:
            continue
        out.append(
            BoardFault(
                kind="smoke_plume",
                title="Smoke staining",
                severity=HealthStatus.HIGH_RISK,
                confidence=float(np.clip(0.40 + frac * 2.6, 0.0, 0.78)),
                bbox=(bx1, by1, bx2, by2),
                plain=(
                    "There is a smoke-like stain spreading upward from one spot. "
                    "That usually means something inside has burned. "
                    "Switch this circuit off and call an electrician."
                ),
                evidence=(
                    f"Vertical fading dark column, {frac * 100:.1f}% of the board, taller than wide "
                    "and smooth — consistent with a smoke deposit."
                ),
            )
        )
    return out


def _detect_arc_damage(roi: np.ndarray) -> list[BoardFault]:
    """Arc-flash pitting: a small blown-out spot ringed by dark residue.

    The dark ring is essential. A bright spot alone is a reflection, a lit
    indicator lamp, or a window — all extremely common on a board.
    """
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1].astype(np.float32)
    val = hsv[:, :, 2].astype(np.float32)
    ref, spread = _surface_stats(roi)

    blown = ((val > 243.0) & (sat < 60.0)).astype(np.uint8)
    if int(np.count_nonzero(blown)) == 0:
        return []
    dark_ring = (val < max(28.0, ref - 2.2 * spread)).astype(np.float32)
    ring_near = cv2.blur(dark_ring, (17, 17))

    mask = ((blown > 0) & (ring_near > 0.16)).astype(np.uint8) * 255
    out: list[BoardFault] = []
    for bx1, by1, bx2, by2, frac in _boxes(mask, 0.0002, 0.02, min_fill=0.20):
        out.append(
            BoardFault(
                kind="arc_damage",
                title="Possible electrical arcing damage",
                severity=HealthStatus.HIGH_RISK,
                confidence=float(np.clip(0.42 + frac * 8.0, 0.0, 0.75)),
                bbox=(bx1, by1, bx2, by2),
                plain=(
                    "There is a small bright pit surrounded by dark residue. "
                    "This can be left behind when electricity jumps a gap and sparks. "
                    "Have an electrician check this point."
                ),
                evidence="Blown-out highlight enclosed by a dark halo — the signature of arc pitting.",
            )
        )
    return out


def _detect_exposed_conductor(roi: np.ndarray) -> list[BoardFault]:
    """Bare copper: the specific warm metallic hue, in an elongated shape.

    Copper's hue band is narrow and distinctive. The elongation requirement
    keeps out brass screws, warm-toned labels and beige plastic.
    """
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.float32) * 2.0
    sat = hsv[:, :, 1].astype(np.float32)
    val = hsv[:, :, 2].astype(np.float32)

    # Bright is load-bearing here. Rust and brown scorch marks share copper's
    # hue band, so hue alone reports "bare wire" on a burn and on a water
    # stain — both observed in testing. Bare copper is *metallic*: it reflects,
    # so it is bright. Corrosion and char are dark and matte.
    copper = ((hue > 14.0) & (hue < 36.0) & (sat > 90.0) & (val > 125.0)).astype(np.uint8) * 255
    out: list[BoardFault] = []
    for bx1, by1, bx2, by2, frac in _boxes(copper, 0.0008, 0.06, min_fill=0.28):
        bw, bh = bx2 - bx1, by2 - by1
        elong = max(bw, bh) / float(max(1, min(bw, bh)))
        if elong < 2.5:  # a blob, not a conductor
            continue
        # A conductor is a *thin* run. A broad elongated patch is a stain.
        if min(bw, bh) > max(6, int(0.10 * min(roi.shape[0], roi.shape[1]))):
            continue
        out.append(
            BoardFault(
                kind="exposed_conductor",
                title="Bare wire showing",
                severity=HealthStatus.HIGH_RISK,
                confidence=float(np.clip(0.38 + frac * 5.0, 0.0, 0.72)),
                bbox=(bx1, by1, bx2, by2),
                plain=(
                    "A bare metal wire appears to be uncovered. "
                    "Touching it could give a serious shock. Keep hands and objects away "
                    "and get it covered properly by an electrician."
                ),
                evidence=f"Elongated copper-toned region ({elong:.1f}:1) with no insulation colour over it.",
            )
        )
    return out


def _detect_loose_wire(roi: np.ndarray) -> list[BoardFault]:
    """Wires leaving the tidy rectilinear pattern of a wired board.

    Inside a board, conductors are dressed into horizontal and vertical runs.
    A long *diagonal* line is either a wire pulled free of its route or one
    hanging out of a terminal. Straightness plus a diagonal angle is the signal.
    """
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    edges = cv2.Canny(cv2.bilateralFilter(gray, 5, 45, 45), 55, 155)
    min_len = int(max(28, min(h, w) * 0.26))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=48, minLineLength=min_len, maxLineGap=6)
    if lines is None:
        return []

    diagonals: list[tuple[int, int, int, int, float]] = []
    for line in lines[:220]:
        lx1, ly1, lx2, ly2 = line[0]
        ang = abs(np.degrees(np.arctan2(ly2 - ly1, lx2 - lx1))) % 180.0
        # keep only clearly diagonal runs (board hardware is axis-aligned)
        if not (28.0 < ang < 62.0 or 118.0 < ang < 152.0):
            continue
        length = float(np.hypot(lx2 - lx1, ly2 - ly1))
        diagonals.append((int(lx1), int(ly1), int(lx2), int(ly2), length))

    if len(diagonals) < 2:  # a single diagonal edge is far too weak to report
        return []
    diagonals.sort(key=lambda d: d[4], reverse=True)
    picked = diagonals[:3]
    xs = [v for d in picked for v in (d[0], d[2])]
    ys = [v for d in picked for v in (d[1], d[3])]
    longest = picked[0][4] / float(max(h, w))

    return [
        BoardFault(
            kind="loose_wire",
            title="Wire looks loose or out of place",
            severity=HealthStatus.WARNING,
            confidence=float(np.clip(0.32 + longest * 0.55, 0.0, 0.62)),
            bbox=(min(xs), min(ys), max(xs), max(ys)),
            plain=(
                "One or more wires look loose or run at an odd angle instead of being tucked in neatly. "
                "Loose wires can heat up over time. Worth having an electrician tidy and tighten them."
            ),
            evidence=f"{len(diagonals)} long diagonal wire-like lines where board wiring is normally straight.",
        )
    ]


def _detect_moisture(roi: np.ndarray) -> list[BoardFault]:
    """Water ingress: rust staining, or a running streak down the surface."""
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.float32) * 2.0
    sat = hsv[:, :, 1].astype(np.float32)
    val = hsv[:, :, 2].astype(np.float32)

    rust = ((hue > 5.0) & (hue < 28.0) & (sat > 95.0) & (val > 45.0) & (val < 190.0)).astype(np.uint8) * 255
    out: list[BoardFault] = []
    for bx1, by1, bx2, by2, frac in _boxes(rust, 0.003, 0.20, min_fill=0.30):
        bw, bh = bx2 - bx1, by2 - by1
        if bh < bw * 0.7:  # water runs downward
            continue
        out.append(
            BoardFault(
                kind="moisture",
                title="Signs of water or rust",
                severity=HealthStatus.WARNING,
                confidence=float(np.clip(0.36 + frac * 2.2, 0.0, 0.68)),
                bbox=(bx1, by1, bx2, by2),
                plain=(
                    "There are rust-coloured streaks that suggest water has got in. "
                    "Water and electricity together are dangerous. Find where the water comes from "
                    "and have the board checked."
                ),
                evidence=f"Downward rust-toned streak covering {frac * 100:.1f}% of the board.",
            )
        )
    return out


def _detect_open_slot(roi: np.ndarray) -> list[BoardFault]:
    """A missing blanking plate — a dark rectangular void in the device row.

    Reported as a warning, not a hazard claim: an open slot exposes live parts
    to fingers and dust, but it is also sometimes simply an unused way with a
    cover we cannot see from this angle.
    """
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
    ref, spread = _surface_stats(roi)
    void = ((gray < max(24.0, ref - 2.6 * spread))).astype(np.uint8) * 255
    out: list[BoardFault] = []
    for bx1, by1, bx2, by2, frac in _boxes(void, 0.0015, 0.05, min_fill=0.62):
        bw, bh = bx2 - bx1, by2 - by1
        aspect = bw / float(max(1, bh))
        # slot-shaped: a tidy rectangle roughly the proportion of one device way
        if not (0.25 < aspect < 3.2):
            continue
        out.append(
            BoardFault(
                kind="open_slot",
                title="Open gap in the board",
                severity=HealthStatus.WARNING,
                confidence=float(np.clip(0.30 + frac * 4.0, 0.0, 0.58)),
                bbox=(bx1, by1, bx2, by2),
                plain=(
                    "There seems to be an open gap where a cover or switch should be. "
                    "Live parts behind it could be touched by fingers, and dust can get in. "
                    "Ask an electrician to fit a blanking cover."
                ),
                evidence=f"Dark rectangular void ({aspect:.1f}:1) filling its outline — consistent with a missing cover.",
            )
        )
    return out
