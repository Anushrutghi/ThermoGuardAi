"""Heat-risk estimation for a switch board.

THE HONESTY CONTRACT
--------------------
A phone camera has no thermal sensor. It cannot measure temperature, and this
module never pretends otherwise.

What it does instead: it looks for **visual evidence consistent with heat
damage** on the board — thermal discoloration of plastics, brown charring, and
arc-flash pitting — and turns that into a 0-100 *heat-risk index* with a
per-zone HOT / WARM / COOL map.

Only three cues survive here, and that is deliberate. Two others (melt gloss,
soot plume) were implemented, measured against a clean reference board, found
to fire on healthy hardware, and removed — see the note in ``_heat_cue_maps``.
A cue that cannot distinguish a breaker from a burn is worse than no cue.

Every value produced here is stamped ``measurement="ESTIMATED_FROM_IMAGE"`` and
``is_temperature=False``. No field ever carries a °C value derived from RGB.

If a genuine thermal frame is supplied (FLIR Lepton, MLX90640, AMG8833, Seek,
or an uploaded radiometric image), the analyser switches to the measured path:
``measurement="MEASURED_C"``, ``is_temperature=True``, and real temperatures are
reported alongside the same zone map. That is the only way a °C ever appears.

Why "COOL" does not mean "cold"
-------------------------------
Zone labels describe *visual heat evidence*, not temperature. A COOL zone means
"no visual signs of heat damage here" — the metal could still be hot. Each zone
therefore carries a plain-language ``meaning`` string, and the result carries a
``disclaimer`` that the UI and the PDF are both required to display.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# --- provenance markers (never invent a third value) ---
ESTIMATED = "ESTIMATED_FROM_IMAGE"
MEASURED = "MEASURED_C"

# --- risk bands on the 0..100 index ---
LOW = "LOW"
MODERATE = "MODERATE"
HIGH = "HIGH"
SEVERE = "SEVERE"

_BAND_EDGES = ((70.0, SEVERE), (45.0, HIGH), (22.0, MODERATE))

# --- zone labels (visual evidence, not temperature) ---
HOT = "HOT"
WARM = "WARM"
COOL = "COOL"

_ZONE_MEANING = {
    HOT: "Clear visual signs of heat damage here.",
    WARM: "Some discoloration or marking — worth a closer look.",
    COOL: "No visual signs of heat damage. (Not a temperature reading.)",
}

DISCLAIMER = (
    "Estimated from the camera image — this is NOT a temperature measurement. "
    "A phone camera cannot measure heat. Connect a thermal camera for real °C readings. "
    "Always use a qualified electrician for live electrical work."
)

MEASURED_DISCLAIMER = (
    "Temperatures measured by a connected thermal sensor. Readings depend on emissivity, "
    "distance and angle. Always use a qualified electrician for live electrical work."
)


@dataclass
class HeatZone:
    """One cell of the board's heat map."""

    row: int
    col: int
    label: str  # HOT | WARM | COOL
    score: float  # 0..1 visual heat evidence
    bbox_norm: tuple[float, float, float, float]  # (x, y, w, h) within the FRAME
    temp_c: float | None = None  # only ever set on the measured path
    drivers: list[str] = field(default_factory=list)  # which cues fired

    @property
    def meaning(self) -> str:
        return _ZONE_MEANING[self.label]

    def to_dict(self) -> dict:
        x, y, w, h = self.bbox_norm
        return {
            "row": self.row,
            "col": self.col,
            "label": self.label,
            "score": round(self.score, 3),
            "meaning": self.meaning,
            "bbox_norm": [round(x, 4), round(y, 4), round(w, 4), round(h, 4)],
            "temp_c": round(self.temp_c, 1) if self.temp_c is not None else None,
            "drivers": list(self.drivers),
        }


@dataclass
class HeatRiskResult:
    """Board-level heat assessment."""

    available: bool = False
    heat_risk: float = 0.0  # 0..100
    band: str = LOW
    measurement: str = ESTIMATED
    is_temperature: bool = False
    zones: list[HeatZone] = field(default_factory=list)
    grid: tuple[int, int] = (0, 0)  # (rows, cols)
    hotspot: tuple[float, float] | None = None  # normalized (x, y) in the frame
    hot_zone_count: int = 0
    warm_zone_count: int = 0
    spread: str = "none"  # none | localized | multiple | widespread
    evidence: list[str] = field(default_factory=list)
    # measured path only
    max_temp_c: float | None = None
    min_temp_c: float | None = None
    avg_temp_c: float | None = None
    delta_c: float | None = None
    emissivity: float | None = None
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "heat_risk": round(self.heat_risk, 1),
            "band": self.band,
            "measurement": self.measurement,
            "is_temperature": self.is_temperature,
            "zones": [z.to_dict() for z in self.zones],
            "grid": {"rows": self.grid[0], "cols": self.grid[1]},
            "hotspot": ({"x": round(self.hotspot[0], 4), "y": round(self.hotspot[1], 4)} if self.hotspot else None),
            "hot_zone_count": self.hot_zone_count,
            "warm_zone_count": self.warm_zone_count,
            "spread": self.spread,
            "evidence": list(self.evidence),
            "max_temp_c": round(self.max_temp_c, 1) if self.max_temp_c is not None else None,
            "min_temp_c": round(self.min_temp_c, 1) if self.min_temp_c is not None else None,
            "avg_temp_c": round(self.avg_temp_c, 1) if self.avg_temp_c is not None else None,
            "delta_c": round(self.delta_c, 1) if self.delta_c is not None else None,
            "emissivity": self.emissivity,
            "disclaimer": self.disclaimer,
        }


class HeatRiskAnalyzer:
    """Turns a board region into a heat-risk index and a zone map.

    ``hot_threshold`` / ``warm_threshold`` gate the per-zone labels on the
    estimated path. They are relative to the 0..1 evidence score.
    """

    def __init__(self, hot_threshold: float = 0.42, warm_threshold: float = 0.20) -> None:
        self.hot_threshold = hot_threshold
        self.warm_threshold = warm_threshold

    # ------------------------------------------------------------------
    def analyze(
        self,
        frame: np.ndarray,
        board_bbox: tuple[int, int, int, int] | None = None,
        temps: np.ndarray | None = None,
        emissivity: float | None = None,
        ambient_c: float | None = None,
    ) -> HeatRiskResult:
        """Assess heat across the board.

        ``frame``       BGR image.
        ``board_bbox``  pixel (x1, y1, x2, y2); defaults to the whole frame.
        ``temps``       optional REAL temperature array (same HxW as frame, or it
                        will be resized). Supplying this switches to the
                        measured path and is the only way °C is ever reported.
        """
        if frame is None or frame.size == 0:
            return HeatRiskResult(available=False, evidence=["No image received."])

        h, w = frame.shape[:2]
        x1, y1, x2, y2 = board_bbox if board_bbox else (0, 0, w, h)
        x1, y1 = max(0, min(x1, w - 2)), max(0, min(y1, h - 2))
        x2, y2 = max(x1 + 2, min(x2, w)), max(y1 + 2, min(y2, h))
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            return HeatRiskResult(available=False, evidence=["Board region was empty."])

        rows, cols = _grid_for(x2 - x1, y2 - y1)

        # An unreadable photo must say "I cannot tell", never a risk number. The
        # dangerous failure here is not a missing answer, it is a confident one:
        # a dark frame used to score 100/SEVERE purely because it was dark.
        if temps is None or not getattr(temps, "size", 0):
            legible, why = _legibility(roi)
            if not legible:
                return HeatRiskResult(
                    available=False,
                    measurement=ESTIMATED,
                    is_temperature=False,
                    grid=(rows, cols),
                    evidence=[why],
                    disclaimer=DISCLAIMER,
                )

        cue_maps = _heat_cue_maps(roi)

        temp_roi = None
        if temps is not None and temps.size:
            t = np.asarray(temps, dtype=np.float32)
            if t.shape[:2] != (h, w):
                t = cv2.resize(t, (w, h), interpolation=cv2.INTER_LINEAR)
            if np.isfinite(t).any():
                finite = t[np.isfinite(t)]
                t = np.where(np.isfinite(t), t, float(np.median(finite))).astype(np.float32)
                temp_roi = t[y1:y2, x1:x2]

        zones = self._build_zones(cue_maps, temp_roi, rows, cols, (x1, y1, x2, y2), (w, h), ambient_c)
        if temp_roi is not None:
            return self._finalize_measured(zones, temp_roi, (rows, cols), emissivity, ambient_c)
        return self._finalize_estimated(zones, (rows, cols))

    # ------------------------------------------------------------------
    def _build_zones(
        self,
        cue_maps: dict[str, np.ndarray],
        temp_roi: np.ndarray | None,
        rows: int,
        cols: int,
        box: tuple[int, int, int, int],
        frame_wh: tuple[int, int],
        ambient_c: float | None,
    ) -> list[HeatZone]:
        x1, y1, x2, y2 = box
        fw, fh = frame_wh
        rh, rw = (y2 - y1) / rows, (x2 - x1) / cols
        combined = cue_maps["combined"]
        ch, cw = combined.shape[:2]

        # measured path thresholds are absolute (delta over the board's own
        # cool baseline), so compute that baseline once
        t_base = None
        if temp_roi is not None:
            t_base = float(np.percentile(temp_roi, 25)) if ambient_c is None else float(ambient_c)

        zones: list[HeatZone] = []
        for r in range(rows):
            for c in range(cols):
                cy1, cy2 = int(r * ch / rows), int((r + 1) * ch / rows)
                cx1, cx2 = int(c * cw / cols), int((c + 1) * cw / cols)
                cell = combined[cy1:max(cy1 + 1, cy2), cx1:max(cx1 + 1, cx2)]
                # 90th percentile: a real hotspot is a concentrated patch, but a
                # single noisy pixel must not label the whole zone.
                score = float(np.percentile(cell, 90)) if cell.size else 0.0
                drivers = _zone_drivers(cue_maps, cy1, cy2, cx1, cx2)

                temp_c = None
                if temp_roi is not None:
                    th, tw = temp_roi.shape[:2]
                    ty1, ty2 = int(r * th / rows), int((r + 1) * th / rows)
                    tx1, tx2 = int(c * tw / cols), int((c + 1) * tw / cols)
                    tcell = temp_roi[ty1:max(ty1 + 1, ty2), tx1:max(tx1 + 1, tx2)]
                    if tcell.size:
                        temp_c = float(np.percentile(tcell, 95))

                if temp_c is not None and t_base is not None:
                    d = temp_c - t_base
                    label = HOT if d >= 20.0 else WARM if d >= 8.0 else COOL
                else:
                    label = HOT if score >= self.hot_threshold else WARM if score >= self.warm_threshold else COOL

                zones.append(
                    HeatZone(
                        row=r,
                        col=c,
                        label=label,
                        score=score,
                        bbox_norm=((x1 + c * rw) / fw, (y1 + r * rh) / fh, rw / fw, rh / fh),
                        temp_c=temp_c,
                        drivers=drivers,
                    )
                )
        return zones

    # ------------------------------------------------------------------
    def _finalize_estimated(self, zones: list[HeatZone], grid: tuple[int, int]) -> HeatRiskResult:
        hot = [z for z in zones if z.label == HOT]
        warm = [z for z in zones if z.label == WARM]
        worst = max(zones, key=lambda z: z.score, default=None)
        peak = worst.score if worst else 0.0

        # The index is driven by the strongest single piece of evidence. Breadth
        # only amplifies once there is at least one genuinely HOT zone —
        # otherwise a board with mild variation across many cells could climb
        # into HIGH on no real evidence at all, which is precisely the false
        # alarm that makes a safety tool worthless.
        risk = peak * 72.0
        if hot:
            involvement = (len(hot) + 0.35 * len(warm)) / max(1, len(zones))
            risk += involvement * 55.0
        else:
            # warm-only evidence is a "worth a look", never an alarm
            risk = min(risk, 30.0) + min(6.0, 0.6 * len(warm))
        risk = float(np.clip(risk, 0.0, 100.0))

        band = LOW
        for edge, name in _BAND_EDGES:
            if risk >= edge:
                band = name
                break
        # Structural cap: SEVERE/HIGH assert real damage is visible. Without a
        # single HOT zone the evidence cannot support that claim.
        if not hot and band in (SEVERE, HIGH):
            band = MODERATE

        spread = _spread_label(len(hot), len(warm), len(zones))
        evidence = _estimated_evidence(hot, warm, zones)

        return HeatRiskResult(
            available=True,
            heat_risk=risk,
            band=band,
            measurement=ESTIMATED,
            is_temperature=False,
            zones=zones,
            grid=grid,
            hotspot=_zone_center(worst) if worst and worst.score > 0.18 else None,
            hot_zone_count=len(hot),
            warm_zone_count=len(warm),
            spread=spread,
            evidence=evidence,
            disclaimer=DISCLAIMER,
        )

    def _finalize_measured(
        self,
        zones: list[HeatZone],
        temp_roi: np.ndarray,
        grid: tuple[int, int],
        emissivity: float | None,
        ambient_c: float | None,
    ) -> HeatRiskResult:
        hot = [z for z in zones if z.label == HOT]
        warm = [z for z in zones if z.label == WARM]
        worst = max(zones, key=lambda z: (z.temp_c if z.temp_c is not None else -1e9), default=None)

        max_t = float(np.max(temp_roi))
        min_t = float(np.min(temp_roi))
        avg_t = float(np.mean(temp_roi))
        base = float(ambient_c) if ambient_c is not None else float(np.percentile(temp_roi, 25))
        delta = max_t - base

        # Standard thermographic severity on delta-over-reference.
        if delta >= 35.0:
            risk, band = 88.0, SEVERE
        elif delta >= 20.0:
            risk, band = 62.0, HIGH
        elif delta >= 10.0:
            risk, band = 34.0, MODERATE
        else:
            risk, band = max(4.0, delta * 1.2), LOW

        evidence = [
            f"Hottest point on the board measured {max_t:.1f} °C.",
            f"That is {delta:.1f} °C above the cooler reference area ({base:.1f} °C).",
        ]
        if hot:
            evidence.append(f"{len(hot)} area(s) are significantly hotter than the rest of the board.")

        return HeatRiskResult(
            available=True,
            heat_risk=float(np.clip(risk, 0.0, 100.0)),
            band=band,
            measurement=MEASURED,
            is_temperature=True,
            zones=zones,
            grid=grid,
            hotspot=_zone_center(worst) if worst else None,
            hot_zone_count=len(hot),
            warm_zone_count=len(warm),
            spread=_spread_label(len(hot), len(warm), len(zones)),
            evidence=evidence,
            max_temp_c=max_t,
            min_temp_c=min_t,
            avg_temp_c=avg_t,
            delta_c=delta,
            emissivity=emissivity,
            disclaimer=MEASURED_DISCLAIMER,
        )


# ----------------------------------------------------------------------
# Visual heat cues
# ----------------------------------------------------------------------
def _heat_cue_maps(roi: np.ndarray) -> dict[str, np.ndarray]:
    """Per-pixel evidence maps for each visual heat-damage cue.

    EVERY CUE IS RELATIVE TO THE BOARD'S OWN SURFACE.

    That is not a stylistic choice, it is the whole correctness argument. An
    earlier version of this function used absolute thresholds — ``val < 0.34``
    meant "charred", a bright smooth patch meant "melted". The result was that a
    perfectly clean board scored 47/100 HIGH and an under-exposed photo scored
    100/100 SEVERE, because plain board surface *is* smooth and a dark photo
    *is* dark. Absolute thresholds cannot work here: boards range from white
    plastic to dark grey steel to bare metal, and phone auto-exposure and white
    balance move the whole histogram around.

    So each cue asks "how far does this pixel depart from what the rest of THIS
    board looks like", and a uniform surface — at any brightness — departs from
    itself by nothing and scores zero.
    """
    work_w = 320
    h, w = roi.shape[:2]
    if w > work_w:
        s = work_w / float(w)
        roi = cv2.resize(roi, (0, 0), fx=s, fy=s, interpolation=cv2.INTER_AREA)
    roi = cv2.bilateralFilter(roi, 5, 45, 45)

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.float32) * 2.0  # OpenCV packs hue into 0..179
    sat = hsv[:, :, 1].astype(np.float32) / 255.0
    val = hsv[:, :, 2].astype(np.float32) / 255.0
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)

    # --- the board's own reference surface ---
    # 60th percentile brightness is the healthy face (devices and shadows sit
    # below it, glare above). The spread tells us how much natural variation
    # this particular board already has, so a busy industrial cabinet is not
    # judged by the standard of a blank plate.
    ref_val = float(np.percentile(val, 60))
    spread = float(np.std(val)) + 0.02
    ref_sat = float(np.percentile(sat, 60))

    # How far darker than its own board a pixel is, in units of the board's own
    # variation. Needs > ~2 sigma to count at all.
    darker_sigma = np.clip((ref_val - val) / (2.2 * spread), 0, 1)

    # 1. Thermal discoloration — plastics brown/amber, copper darkens to bronze.
    #    Requires the amber hue AND more saturation than the board's own
    #    surface: a warm-white-balanced grey board is not "discoloured".
    amber = np.exp(-((hue - 30.0) ** 2) / (2 * 14.0**2))
    excess_sat = np.clip((sat - ref_sat - 0.12) / 0.22, 0, 1)
    discolor = np.clip(amber * excess_sat * np.clip(darker_sigma + 0.25, 0, 1) * 1.35, 0, 1)

    # 2. Charring / soot — much darker than the board, flat, AND BROWN-TINTED.
    #    The brown requirement is essential and was learned the hard way: a
    #    board is covered in solid black plastic breaker bodies, which are
    #    darker than the plate and perfectly flat. Without a hue test they read
    #    as charring and a clean board scores SEVERE. Neutral dark is hardware;
    #    brown dark is a burn.
    local_var = cv2.blur((gray - cv2.blur(gray, (9, 9))) ** 2, (9, 9))
    typical_var = float(np.median(local_var)) + 4.0
    flatness = np.clip(1.0 - local_var / (2.5 * typical_var), 0, 1)
    # "Brown" needs BOTH an absolute saturation floor and more saturation than
    # the board. The absolute floor is what stops near-neutral dark grey plastic
    # from qualifying: a breaker body at BGR (70,72,78) computes to hue 15° —
    # nominally brown — but its saturation is only 0.10. Real scorching is
    # visibly coloured. Relative-only testing passed that grey and made every
    # breaker on a clean board look charred.
    truly_brown = (hue > 8.0) & (hue < 48.0) & (sat > 0.22)
    brown_tint = truly_brown.astype(np.float32) * np.clip((sat - ref_sat) / 0.18, 0, 1)
    char = np.clip(np.clip((ref_val - val) / (3.0 * spread), 0, 1) * flatness * brown_tint * 1.4, 0, 1)

    # 3. Arc-flash pitting — a blown-out highlight ringed by dark residue. The
    #    dark ring is mandatory: a bare highlight is a reflection or an
    #    indicator lamp, both of which are everywhere on a real board.
    blown = ((val > 0.95) & (sat < 0.22)).astype(np.float32)
    if float(blown.sum()) > 0:
        blown = cv2.dilate(blown, np.ones((3, 3), np.uint8))
        halo = cv2.blur(np.clip((ref_val - val) / (2.6 * spread), 0, 1), (13, 13))
        arc = np.clip(cv2.blur(blown, (9, 9)) * halo * 7.0, 0, 1)
    else:
        arc = np.zeros_like(val)

    # TWO CUES WERE DELIBERATELY REMOVED, both for the same reason: they were
    # not specific to heat damage on a switch board, and no relative
    # reformulation fixed that.
    #
    #   "melt / deformation gloss" — looked for a glossy patch that had lost
    #   surrounding structure. A board's face is covered in glossy moulded
    #   plastic toggles and labels, every one of which matches.
    #
    #   "soot plume" — looked for brightness fading upward from a source. A
    #   board is built as horizontal bands of alternating brightness (rows of
    #   dark devices on light rails), so it is one large vertical gradient by
    #   construction. This cue measured 0.99 on a perfectly clean board at fine
    #   scale and 1.00 at coarse scale.
    #
    # Smoke staining is still detected — in ``wire_faults._detect_smoke_plume``,
    # where a connected-region shape test (taller than wide, localised, densely
    # filled) can discriminate a real plume from board banding. A per-pixel map
    # cannot. Capability kept; the cry-wolf version dropped.

    weights = {"discolor": 1.00, "char": 0.95, "arc": 0.85}
    maps = {"discolor": discolor, "char": char, "arc": arc}
    stack = np.stack([np.clip(maps[k] * weights[k], 0, 1) for k in maps], axis=0)
    # Max-dominant blend: one strong, specific cue is the real signal. The mean
    # term is small so that broad weak agreement cannot manufacture a hotspot.
    combined = np.clip(stack.max(axis=0) * 0.90 + stack.mean(axis=0) * 0.25, 0, 1)
    combined = cv2.blur(combined, (7, 7))

    maps["combined"] = combined.astype(np.float32)
    return {k: v.astype(np.float32) for k, v in maps.items()}


def _zone_drivers(cue_maps: dict[str, np.ndarray], y1: int, y2: int, x1: int, x2: int) -> list[str]:
    """Which cues meaningfully fired in this cell (for plain-language output)."""
    names = {
        "discolor": "heat discoloration",
        "char": "charring or soot",

        "arc": "arc marks",
        
    }
    out: list[str] = []
    for key, label in names.items():
        m = cue_maps.get(key)
        if m is None:
            continue
        cell = m[y1:max(y1 + 1, y2), x1:max(x1 + 1, x2)]
        if cell.size and float(np.percentile(cell, 90)) >= 0.30:
            out.append(label)
    return out


def _legibility(roi: np.ndarray) -> tuple[bool, str]:
    """Can this photo support a heat judgement at all?

    Returning "I cannot tell" is the safe answer. Returning a number from an
    unreadable frame is not, because the user will act on the number.
    """
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mean = float(gray.mean())
    if mean < 38.0:
        return False, "The photo is too dark to judge heat. Turn on a light and take it again."
    if mean > 244.0:
        return False, "The photo is too bright or washed out. Move away from the light and try again."
    if float(cv2.Laplacian(gray, cv2.CV_64F).var()) < 28.0:
        return False, "The photo is too blurry to judge heat. Hold the phone steady and try again."
    if float(gray.std()) < 6.0:
        return False, "The camera could not make out any detail on the board. Move a little closer."
    return True, ""


def _grid_for(width: int, height: int) -> tuple[int, int]:
    """Adaptive zone grid — more cells along the board's longer axis."""
    aspect = width / float(max(1, height))
    if aspect >= 1.6:
        return 3, 6
    if aspect <= 0.62:
        return 6, 3
    return 4, 4


def _spread_label(hot: int, warm: int, total: int) -> str:
    if hot == 0 and warm == 0:
        return "none"
    frac = (hot + warm) / max(1, total)
    if hot <= 1 and frac < 0.20:
        return "localized"
    if frac >= 0.45:
        return "widespread"
    return "multiple"


def _zone_center(zone: HeatZone | None) -> tuple[float, float] | None:
    if zone is None:
        return None
    x, y, w, h = zone.bbox_norm
    return (x + w / 2.0, y + h / 2.0)


def _estimated_evidence(hot: list[HeatZone], warm: list[HeatZone], zones: list[HeatZone]) -> list[str]:
    """Plain sentences explaining WHY the index came out where it did."""
    if not hot and not warm:
        return ["No visual signs of heat damage were found on the board."]

    out: list[str] = []
    drivers: dict[str, int] = {}
    for z in hot + warm:
        for d in z.drivers:
            drivers[d] = drivers.get(d, 0) + 1

    if hot:
        out.append(f"{len(hot)} area(s) show clear visual signs of heat damage.")
    if warm:
        out.append(f"{len(warm)} area(s) show milder discoloration or marking.")
    for name, count in sorted(drivers.items(), key=lambda kv: kv[1], reverse=True)[:3]:
        out.append(f"Signs of {name} found in {count} area(s).")
    out.append("These are signs visible in the photo, not temperature readings.")
    return out
