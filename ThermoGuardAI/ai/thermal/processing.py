"""Thermal data processing: statistics, hotspots, overlays, alignment."""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from ai.thermal.base import ThermalFrame


@dataclass
class ThermalStats:
    """Aggregated thermal statistics for a frame or region."""

    max_temp: float = 0.0
    min_temp: float = 0.0
    avg_temp: float = 0.0
    delta: float = 0.0  # max - min
    hotspot_x: float = 0.0  # normalized 0..1
    hotspot_y: float = 0.0
    gradient: float = 0.0  # max spatial gradient (C/pixel)
    heat_spread: float = 0.0  # fraction of pixels above 60% of range
    data: list[list[float]] = field(default_factory=list)


@dataclass
class CircuitHeatAnalysis:
    """Deep heat analysis of one circuit region.

    Instead of reading one temperature "directly", heat is sampled across the
    whole circuit: the *core* (heat source), the *borders* (the circuit's own
    walls) and an outer *wall ring* (heat spilling into the surrounding wires).
    A circuit is only an overload candidate when heat is genuinely flowing
    outward: hot core → hot borders → heat reaching the wires.
    """

    core_max: float = 0.0  # hottest pixel inside the component
    core_center: float = 0.0  # representative core temperature (90th pct)
    border_max: float = 0.0  # hottest pixel on the component border
    border_mean: float = 0.0  # mean temperature of the border ring
    wall_max: float = 0.0  # hottest pixel in the outer wall/wire ring
    wall_mean: float = 0.0
    flow_outward: float = 0.0  # core_max - wall_mean (heat escaping to wires)
    core_rise: float = 0.0  # core_max above ambient
    border_rise: float = 0.0  # border_mean above ambient
    wall_rise: float = 0.0  # wall_mean above ambient
    background_mean: float = 0.0  # uniform-surface reference (farther out)
    background_std: float = 0.0
    uniformity: float = 0.0  # wall-ring variability vs. background (0 = uniform)
    heat_loss: float = 0.0  # wall_mean above the background reference
    cold_anomaly: float = 0.0  # background - coldest wall pixel (moisture/leak)
    moisture_candidate: bool = False  # water/leak signature (colder than surface)
    score: float = 0.0  # 0..1 composite "deep heat" score
    overload_candidate: bool = False

    def to_json(self) -> dict:
        return {
            "core_max": round(self.core_max, 2),
            "core_center": round(self.core_center, 2),
            "border_max": round(self.border_max, 2),
            "border_mean": round(self.border_mean, 2),
            "wall_max": round(self.wall_max, 2),
            "wall_mean": round(self.wall_mean, 2),
            "flow_outward": round(self.flow_outward, 2),
            "core_rise": round(self.core_rise, 2),
            "border_rise": round(self.border_rise, 2),
            "wall_rise": round(self.wall_rise, 2),
            "background_mean": round(self.background_mean, 2),
            "uniformity": round(self.uniformity, 3),
            "heat_loss": round(self.heat_loss, 2),
            "cold_anomaly": round(self.cold_anomaly, 2),
            "moisture_candidate": self.moisture_candidate,
            "score": round(self.score, 3),
            "overload_candidate": self.overload_candidate,
        }


def analyze_circuit_heat(
    frame: ThermalFrame,
    bbox_norm: tuple[float, float, float, float],
    ambient: float | None = None,
    critical_delta: float = 25.0,
) -> CircuitHeatAnalysis:
    """Deep multi-region heat scan of a detected circuit.

    Samples four concentric regions of the thermal frame:
      * core   — central 50% of the bounding box (the heat source)
      * border — the perimeter ring of the box (the circuit's walls)
      * wall   — a ring just outside the box (heat flowing into the wires)
      * background — a farther-out surface reference (the "uniform surface")

    Overload is only flagged when heat radiates outward (core → border → wall),
    so a single hot pixel or reflection can never trigger a false alarm.
    Following thermographic practice, we read *surface variance* against a
    uniform background rather than any direct single-point measurement — that
    is how heat behind a surface (walls/wiring) is detected. Cold spots in the
    surrounding surface (colder than the background reference) are flagged as
    moisture/leak candidates, since wet areas draw heat away.
    """
    t = frame.temperatures
    h, w = t.shape
    if h == 0 or w == 0:
        return CircuitHeatAnalysis()
    ambient = ambient if ambient is not None else float(np.nanmean(t))

    x, y, bw, bh = bbox_norm
    x1 = int(np.clip(x * w, 0, w - 1))
    y1 = int(np.clip(y * h, 0, h - 1))
    x2 = int(np.clip((x + bw) * w, x1 + 1, w))
    y2 = int(np.clip((y + bh) * h, y1 + 1, h))
    box_w, box_h = x2 - x1, y2 - y1
    if box_w < 2 or box_h < 2:
        return CircuitHeatAnalysis()

    def _safe_max(arr: np.ndarray) -> float:
        return float(np.nanmax(arr)) if arr.size else 0.0

    def _safe_mean(arr: np.ndarray) -> float:
        return float(np.nanmean(arr)) if arr.size else 0.0

    # --- core: central 50% (heat source) ---------------------------------
    cx1, cx2 = x1 + box_w // 4, x2 - box_w // 4
    cy1, cy2 = y1 + box_h // 4, y2 - box_h // 4
    core = t[cy1:cy2, cx1:cx2]
    core_max = _safe_max(core)
    core_center = float(np.nanpercentile(core, 90)) if core.size else 0.0

    # --- border: perimeter ring of the box (the circuit's walls) ---------
    inset_x, inset_y = max(1, box_w // 5), max(1, box_h // 5)
    inner = np.zeros_like(t, dtype=bool)
    ix1, iy1 = x1 + inset_x, y1 + inset_y
    ix2, iy2 = x2 - inset_x, y2 - inset_y
    if ix2 > ix1 and iy2 > iy1:
        inner[iy1:iy2, ix1:ix2] = True
    box = np.zeros_like(t, dtype=bool)
    box[y1:y2, x1:x2] = True
    border = t[box & ~inner]
    border_max = _safe_max(border)
    border_mean = _safe_mean(border)

    # --- wall ring: annulus just outside the box (heat into the wires) ---
    margin_x = max(1, int(box_w * 0.5))
    margin_y = max(1, int(box_h * 0.5))
    wx1, wy1 = max(0, x1 - margin_x), max(0, y1 - margin_y)
    wx2, wy2 = min(w, x2 + margin_x), min(h, y2 + margin_y)
    outer = np.zeros_like(t, dtype=bool)
    outer[wy1:wy2, wx1:wx2] = True
    wall = t[outer & ~box]
    wall_max = _safe_max(wall)
    wall_mean = _safe_mean(wall)
    wall_min = float(np.nanmin(wall)) if wall.size else 0.0

    # --- background: uniform-surface reference (farther out) -------------
    bg_margin_x = max(1, int(box_w * 0.9))
    bg_margin_y = max(1, int(box_h * 0.9))
    bx1, by1 = max(0, x1 - bg_margin_x), max(0, y1 - bg_margin_y)
    bx2, by2 = min(w, x2 + bg_margin_x), min(h, y2 + bg_margin_y)
    bg_outer = np.zeros_like(t, dtype=bool)
    bg_outer[by1:by2, bx1:bx2] = True
    background = t[bg_outer & ~outer]
    background_mean = _safe_mean(background)
    background_std = float(np.nanstd(background)) if background.size else 0.0

    core_rise = core_max - ambient
    border_rise = border_mean - ambient
    wall_rise = wall_mean - ambient
    flow_outward = core_max - wall_mean

    crit = max(1.0, critical_delta)
    # "Uniform surface" principle: a healthy area has uniform temperature;
    # variability in the surrounding wall ring is the anomaly signal.
    uniformity = float(np.nanstd(wall)) / max(0.05, background_std) if wall.size else 0.0
    # Heat loss = the surface immediately around the circuit hotter than the
    # uniform background farther away → energy/heat escaping the circuit.
    heat_loss = wall_mean - background_mean
    # Cold spots: moisture/leaks draw heat away → colder than the surface.
    cold_anomaly = max(0.0, background_mean - wall_min)

    score = float(
        np.clip(
            0.45 * _norm(core_rise / crit)
            + 0.3 * _norm(border_rise / crit)
            + 0.25 * _norm(wall_rise / crit),
            0.0,
            1.0,
        )
    )
    overload_candidate = (
        core_rise >= 0.5 * crit
        and border_rise >= 0.25 * crit
        and wall_rise >= 0.12 * crit
        and flow_outward >= 0.3 * crit
    )
    moisture_candidate = cold_anomaly >= 8.0 and uniformity >= 1.5
    return CircuitHeatAnalysis(
        core_max=core_max,
        core_center=core_center,
        border_max=border_max,
        border_mean=border_mean,
        wall_max=wall_max,
        wall_mean=wall_mean,
        flow_outward=flow_outward,
        core_rise=core_rise,
        border_rise=border_rise,
        wall_rise=wall_rise,
        background_mean=background_mean,
        background_std=background_std,
        uniformity=uniformity,
        heat_loss=heat_loss,
        cold_anomaly=cold_anomaly,
        moisture_candidate=moisture_candidate,
        score=score,
        overload_candidate=overload_candidate,
    )


def _norm(x: float) -> float:
    return float(np.clip(x, 0.0, 1.0))


def compute_stats(frame: ThermalFrame, ambient: float | None = None) -> ThermalStats:
    """Compute full thermal statistics for a frame."""
    t = frame.temperatures
    if t.size == 0:
        return ThermalStats()
    tmax, tmin = float(np.nanmax(t)), float(np.nanmin(t))
    tavg = float(np.nanmean(t))

    # hotspot location
    flat = np.nan_to_num(t, nan=tmin)
    idx = int(np.nanargmax(flat))
    h, w = t.shape
    hy, hx = divmod(idx, w)

    # max spatial gradient magnitude
    gy, gx = np.gradient(flat)
    grad = float(np.max(np.hypot(gx, gy))) if t.size > 1 else 0.0

    # heat spread: fraction of pixels hotter than 60% of (max - min)
    span = tmax - tmin
    spread = float(np.mean(flat >= (tmin + 0.6 * span))) if span > 0.01 else 0.0

    return ThermalStats(
        max_temp=tmax,
        min_temp=tmin,
        avg_temp=tavg,
        delta=tmax - tmin,
        hotspot_x=hx / w,
        hotspot_y=hy / h,
        gradient=grad,
        heat_spread=spread,
        data=flat.tolist(),
    )


def region_temp(frame: ThermalFrame, bbox_norm: tuple[float, float, float, float], percentile: float = 90.0) -> float | None:
    """Representative temperature within a normalized bbox (x, y, w, h) in 0..1.

    Uses the `percentile`-th percentile of the region (default 90th) so a
    small hotspot inside a large component still drives the reading.
    """
    t = frame.temperatures
    h, w = t.shape
    if h == 0 or w == 0:
        return None
    x, y, bw, bh = bbox_norm
    x1 = int(np.clip(x * w, 0, w - 1))
    y1 = int(np.clip(y * h, 0, h - 1))
    x2 = int(np.clip((x + bw) * w, x1 + 1, w))
    y2 = int(np.clip((y + bh) * h, y1 + 1, h))
    region = t[y1:y2, x1:x2]
    if region.size == 0:
        return None
    return float(np.nanpercentile(region, percentile))


def to_colormap(frame: ThermalFrame, alpha: float = 0.55, max_temp: float | None = None) -> np.ndarray:
    """Return an 8-bit BGR overlay image of the thermal map."""
    t = frame.temperatures
    tmax = max_temp or float(np.nanmax(t)) or 60.0
    norm = np.clip((np.nan_to_num(t, nan=0.0)) / tmax, 0.0, 1.0)
    norm8 = (norm * 255).astype(np.uint8)
    colored = cv2.applyColorMap(norm8, cv2.COLORMAP_INFERNO)
    return colored


def overlay_on_rgb(rgb: np.ndarray, thermal_bgr: np.ndarray, alpha: float = 0.45) -> np.ndarray:
    """Blend a thermal colormap onto an RGB frame (thermal resized to RGB).

    Accepts either a 3-channel colormap or a raw 2-D temperature map
    (colormap applied automatically).
    """
    if thermal_bgr.ndim == 2:
        tmax = float(np.nanmax(thermal_bgr)) or 1.0
        norm = np.clip(np.nan_to_num(thermal_bgr, nan=0.0) / tmax, 0.0, 1.0)
        thermal_bgr = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    if thermal_bgr.shape[:2] != rgb.shape[:2]:
        thermal_bgr = cv2.resize(thermal_bgr, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_LINEAR)
    return cv2.addWeighted(rgb, 1.0 - alpha, thermal_bgr, alpha, 0)


def resize_to_rgb(frame: ThermalFrame, shape: tuple[int, int]) -> ThermalFrame:
    """Resample thermal frame to match an RGB frame's (h, w)."""
    h, w = shape
    t = cv2.resize(frame.temperatures, (w, h), interpolation=cv2.INTER_CUBIC)
    return ThermalFrame(temperatures=t, timestamp=frame.timestamp, source=frame.source)
