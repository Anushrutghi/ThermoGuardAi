"""Switch-first thermal analysis (S1).

After the switch is confirmed, thermal frames are stabilized (baseline), then
sampled into a time series. The switch region is compared against the
surrounding-wall reference region (never a universal hardcoded threshold —
all thresholds are configurable). Outputs a conservative anomaly
classification (NORMAL / ELEVATED / ABNORMAL / CRITICAL), a heat-path finding
and an evidence list explaining WHY each finding was made.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from ai.thermal.base import ThermalDataQuality, ThermalFrame, validate_thermal_frame
from backend.core.config import get_settings

logger = logging.getLogger(__name__)

# Conservative classification labels — the system reports *potential* thermal
# anomalies, never an unsupported claim of a confirmed electrical fault.
NORMAL = "NORMAL"
ELEVATED = "ELEVATED"
ABNORMAL = "ABNORMAL"
CRITICAL = "CRITICAL"
UNAVAILABLE = "UNAVAILABLE"

_SEVERITY_SCORE = {NORMAL: 5.0, ELEVATED: 30.0, ABNORMAL: 60.0, CRITICAL: 88.0}


@dataclass
class ThermalScanResult:
    """Final result of the switch thermal scan."""

    thermal_available: bool = False
    switch_temp: float | None = None  # representative switch temperature (°C)
    wall_temp: float | None = None  # surrounding wall reference (°C)
    ambient: float | None = None  # baseline ambient (°C)
    delta_vs_wall: float | None = None  # switch - wall
    max_temp: float | None = None  # hottest pixel in the ROI during the scan
    min_temp: float | None = None  # coldest pixel in the ROI during the scan (S2)
    avg_temp: float | None = None  # mean temperature of the ROI across the scan (S2)
    hotspot: tuple[float, float] | None = None  # normalized (x, y) in frame
    heat_path: str = "unknown"  # localized | extended | unknown
    trend_c_per_min: float | None = None
    trend_delta_c: float | None = None
    rapid_increase: bool = False
    stability_c: float | None = None  # std of switch temps across the scan
    classification: str = UNAVAILABLE
    risk_score: float = 0.0  # 0..100
    evidence: list[str] = field(default_factory=list)
    series: list[dict] = field(default_factory=list)  # [{t, switch, wall}]
    message: str = "Thermal sensor unavailable — connect a thermal camera."
    # S4 data quality: VALID / LOW_CONFIDENCE / INVALID. Risk analysis is
    # never generated from INVALID thermal data.
    data_quality: str = ThermalDataQuality.VALID.value
    quality_reasons: list[str] = field(default_factory=list)
    emissivity: float | None = None  # None = unavailable (never invented)

    def to_dict(self) -> dict:
        hotspot = {"x": round(self.hotspot[0], 3), "y": round(self.hotspot[1], 3)} if self.hotspot else None
        return {
            "thermal_available": self.thermal_available,
            "switch_temp": round(self.switch_temp, 2) if self.switch_temp is not None else None,
            "wall_temp": round(self.wall_temp, 2) if self.wall_temp is not None else None,
            "ambient": round(self.ambient, 2) if self.ambient is not None else None,
            "delta_vs_wall": round(self.delta_vs_wall, 2) if self.delta_vs_wall is not None else None,
            "max_temp": round(self.max_temp, 2) if self.max_temp is not None else None,
            "min_temp": round(self.min_temp, 2) if self.min_temp is not None else None,
            "avg_temp": round(self.avg_temp, 2) if self.avg_temp is not None else None,
            "hotspot": hotspot,
            "heat_path": self.heat_path,
            "trend_c_per_min": round(self.trend_c_per_min, 2) if self.trend_c_per_min is not None else None,
            "trend_delta_c": round(self.trend_delta_c, 2) if self.trend_delta_c is not None else None,
            "rapid_increase": self.rapid_increase,
            "stability_c": round(self.stability_c, 2) if self.stability_c is not None else None,
            "classification": self.classification,
            "risk_score": round(self.risk_score, 1),
            "evidence": self.evidence,
            "series": self.series,
            "message": self.message,
            "data_quality": self.data_quality,
            "quality_reasons": list(self.quality_reasons),
            "emissivity": self.emissivity,
        }


class SwitchThermalAnalyzer:
    """Collects a baseline, then a time-series, then produces the scan result."""

    STAGE_IDLE = "idle"
    STAGE_BASELINE = "baseline"
    STAGE_SCANNING = "scanning"
    STAGE_DONE = "done"

    def __init__(
        self,
        baseline_frames: int | None = None,
        scan_frames: int | None = None,
        elevated_delta_c: float | None = None,
        abnormal_delta_c: float | None = None,
        critical_delta_c: float | None = None,
        rapid_rise_c_per_min: float | None = None,
    ) -> None:
        s = get_settings()
        self.baseline_frames = baseline_frames or s.thermal_baseline_frames
        self.scan_frames = scan_frames or s.thermal_scan_frames
        self.elevated = elevated_delta_c if elevated_delta_c is not None else s.thermal_elevated_delta_c
        self.abnormal = abnormal_delta_c if abnormal_delta_c is not None else s.thermal_abnormal_delta_c
        self.critical = critical_delta_c if critical_delta_c is not None else s.thermal_critical_delta_c
        self.rapid_rise = rapid_rise_c_per_min if rapid_rise_c_per_min is not None else s.thermal_rapid_rise_c_per_min

        self._baseline_means: list[float] = []
        self._series: list[dict] = []
        self._ts: list[float] = []
        self._switch_temps: list[float] = []
        self._wall_temps: list[float] = []
        self._roi_max: list[float] = []
        self._roi_min: list[float] = []
        self._roi_avg: list[float] = []
        self._hotspots: list[tuple[float, float]] = []
        self._result: ThermalScanResult | None = None
        # S4 frame validation state (never feed analytics from INVALID frames)
        self.last_quality = ThermalDataQuality.VALID
        self.quality_reasons: list[str] = []
        # Emissivity of the source that actually produced the frames (None =
        # unavailable — passed explicitly by the controller, never guessed).
        self._emissivity: float | None = None

    # ------------------------------------------------------------------
    @property
    def stage(self) -> str:
        if self._result is not None:
            return self.STAGE_DONE
        if len(self._baseline_means) < self.baseline_frames:
            return self.STAGE_BASELINE
        return self.STAGE_SCANNING

    @property
    def baseline_ready(self) -> bool:
        return len(self._baseline_means) >= self.baseline_frames

    def add(
        self,
        thermal: ThermalFrame,
        switch_bbox_norm: tuple[float, float, float, float] | None,
        roi: tuple[int, int, int, int],
        frame_shape: tuple[int, int],
        emissivity: float | None = None,
    ) -> ThermalScanResult | None:
        """Feed one thermal frame. Returns the final result once the scan ends.

        ``emissivity`` — the active source's emissivity (None = unavailable);
        recorded on the final result so it always matches the sensor that
        produced the frames.
        """
        if self._result is not None:
            return self._result
        self._emissivity = emissivity
        temps = thermal.temperatures
        if temps.size == 0:
            self.last_quality = ThermalDataQuality.INVALID
            self.quality_reasons = ["Empty thermal frame"]
            return None

        # S4 frame validation — invalid frames are discarded here and can never
        # contaminate the baseline, the time-series or the risk classification.
        settings = get_settings()
        validation = validate_thermal_frame(
            temps,
            min_temp_c=settings.thermal_frame_min_c,
            max_temp_c=settings.thermal_frame_max_c,
            max_invalid_fraction=settings.thermal_max_invalid_fraction,
        )
        self.last_quality = validation.quality
        self.quality_reasons = list(validation.reasons)
        if validation.quality == ThermalDataQuality.INVALID:
            # Sensor likely disconnected / corrupted — do not silently fall
            # back to DEMO; surface the reason and wait for a valid frame.
            logger.warning("Discarding invalid thermal frame: %s", "; ".join(validation.reasons))
            return None

        h, w = frame_shape[:2]
        if temps.shape != (h, w):
            # NOTE: resizing must happen BEFORE masking — resize_to_rgb reads the
            # ORIGINAL frame, so masking first would be undone by the resize.
            from ai.thermal.processing import resize_to_rgb

            temps = np.asarray(resize_to_rgb(thermal, (h, w)).temperatures, dtype=np.float32)

        # S4 policy (documented): a frame is INVALID and fully excluded when
        # (a) it contains NO usable pixels, (b) any value is outside the
        # thermographic range, or (c) the non-finite fraction exceeds
        # THERMAL_MAX_INVALID_FRACTION. Sparse non-finite pixels (≤ threshold)
        # are LOW_CONFIDENCE: they are explicitly MASKED with the frame's own
        # finite median AFTER resizing (so the exact array that feeds every
        # statistic is finite) — NaN/±Inf can never enter analytics or
        # persistence.
        if not np.isfinite(temps).all():
            finite = temps[np.isfinite(temps)]
            fill = float(np.median(finite)) if finite.size else 0.0
            temps = np.where(np.isfinite(temps), temps, fill).astype(np.float32)
            # Defensive guarantee: masking must always produce a finite array.
            # If it ever does not, treat the frame as INVALID and discard it
            # rather than risk a NaN reaching any calculation.
            if not np.isfinite(temps).all():
                self.last_quality = ThermalDataQuality.INVALID
                self.quality_reasons = ["Masking failed — non-finite values remain in thermal frame"]
                logger.warning("Discarding thermal frame: masking failed (non-finite values remain)")
                return None

        if not self.baseline_ready:
            # Ambient reference: sample the four frame corners (15% each side)
            # rather than the full frame — the guided hotspot in the ROI would
            # otherwise inflate the baseline.
            self._baseline_means.append(_corner_reference_mean(temps))
            return None

        # Sampling stage: switch region vs surrounding wall reference
        from ai.switch.roi import sample_regions

        regions = sample_regions(temps, roi, frame_shape)
        wall = regions["wall"]
        switch_region = temps
        if switch_bbox_norm is not None:
            bx, by, bw, bh = switch_bbox_norm
            bx1, by1 = int(bx * w), int(by * h)
            bx2, by2 = min(w, int((bx + bw) * w)), min(h, int((by + bh) * h))
            if by2 > by1 and bx2 > bx1:
                switch_region = temps[by1:by2, bx1:bx2]

        # Representative switch temperature: the 95th percentile of the switch
        # box — captures a real hotspot (heat concentrated on the switch) while
        # staying robust to single-pixel sensor noise.
        switch_temp = float(np.nanpercentile(switch_region, 95)) if switch_region.size else float(np.nanmax(temps))
        wall_temp = float(np.nanmean(wall)) if wall.size else float(np.nanmean(temps))
        roi_arr = regions["roi"]
        roi_max = float(np.nanmax(roi_arr)) if roi_arr.size else switch_temp
        roi_min = float(np.nanmin(roi_arr)) if roi_arr.size else switch_temp
        roi_avg = float(np.nanmean(roi_arr)) if roi_arr.size else switch_temp
        # hotspot = hottest pixel inside the ROI, normalized
        flat_idx = int(np.nanargmax(roi_arr)) if roi_arr.size else int(np.nanargmax(temps))
        hy, hx = divmod(flat_idx, roi_arr.shape[1]) if roi_arr.size and roi_arr.ndim == 2 else (0, 0)
        hot_x = (roi[0] + hx) / w
        hot_y = (roi[1] + hy) / h

        now = thermal.timestamp or float(len(self._ts))
        self._ts.append(now)
        self._switch_temps.append(switch_temp)
        self._wall_temps.append(wall_temp)
        self._roi_max.append(roi_max)
        self._roi_min.append(roi_min)
        self._roi_avg.append(roi_avg)
        self._hotspots.append((hot_x, hot_y))
        self._series.append({"t": round(now, 2), "switch": round(switch_temp, 2), "wall": round(wall_temp, 2)})

        if len(self._switch_temps) >= self.scan_frames:
            self._result = self._finalize()
            return self._result
        return None

    # ------------------------------------------------------------------
    def _finalize(self) -> ThermalScanResult:
        ambient = float(np.median(self._baseline_means))
        switch_temp = float(np.median(self._switch_temps[-3:]))  # end-of-scan value
        wall_temp = float(np.median(self._wall_temps[-3:]))
        delta = switch_temp - wall_temp
        max_temp = float(max(self._roi_max))
        min_temp = float(min(self._roi_min))
        avg_temp = float(np.mean(self._roi_avg))
        # most persistent hotspot (median position of the last samples)
        hx = float(np.median([p[0] for p in self._hotspots[-5:]]))
        hy = float(np.median([p[1] for p in self._hotspots[-5:]]))
        stability = float(np.std(self._switch_temps))

        # time-series trend (linear regression)
        n = len(self._switch_temps)
        xs = np.arange(n, dtype=float)
        slope = 0.0
        if n >= 2:
            slope = float(np.polyfit(xs, self._switch_temps, 1)[0])  # °C per sample
        dts = np.diff(self._ts)
        avg_dt = float(np.median(dts)) if len(dts) else 0.0
        if avg_dt > 0.5:
            rate = slope * 60.0 / avg_dt  # °C per minute
        else:
            rate = slope * 30.0  # timestamps absent → assume ~2 samples/second
        trend_delta = float(self._switch_temps[-1] - self._switch_temps[0])
        rapid = rate >= self.rapid_rise and trend_delta > 0

        # classification: switch vs surrounding-wall difference (configurable)
        if delta >= self.critical:
            cls = CRITICAL
        elif delta >= self.abnormal:
            cls = ABNORMAL
        elif delta >= self.elevated:
            cls = ELEVATED
        else:
            cls = NORMAL

        # heat path: does the pattern extend into the surrounding wall?
        heat_path = "unknown"
        evidence: list[str] = []
        if delta >= self.elevated:
            evidence.append(f"Switch at {switch_temp:.1f} °C vs nearby wall {wall_temp:.1f} °C (Δ {delta:+.1f} °C)")
            # extended: the wall ring stays well above ambient (heat reaching
            # the surrounding surface, not just the switch plate)
            spread = float(np.mean([w - ambient for w in self._wall_temps]))
            if spread >= 0.35 * max(1.0, delta) and max_temp - wall_temp >= 0.5 * delta:
                heat_path = "extended"
                evidence.append(
                    "Extended surface thermal pattern into the surrounding wall — "
                    "potentially associated with concealed electrical component/wiring heating. "
                    "Professional inspection recommended."
                )
            else:
                heat_path = "localized"
                evidence.append("Localized hotspot detected on/near the switch plate.")
        else:
            heat_path = "localized"
        if rapid:
            evidence.append(f"Rapid thermal increase detected ({rate:.1f} °C/min) — monitor, not automatically a fault.")

        risk = _SEVERITY_SCORE[cls]
        if rapid and cls in (ELEVATED, ABNORMAL):
            risk += 12.0
        risk = float(min(100.0, risk))

        if cls == CRITICAL:
            message = (
                "Critical thermal anomaly — significant temperature difference between the "
                "switch and its surroundings. Professional electrical inspection is recommended."
            )
        elif cls == ABNORMAL:
            message = "Abnormal thermal pattern detected near the electrical switch. Further professional inspection recommended."
        elif cls == ELEVATED:
            message = "Potential thermal anomaly detected near the electrical switch — monitor the switch."
        else:
            message = "No significant thermal anomaly detected — switch temperature is within expected range."

        # Emissivity: recorded only when the active source genuinely provides
        # it (threaded through from the controller — never guessed globally).
        emissivity = self._emissivity

        return ThermalScanResult(
            thermal_available=True,
            switch_temp=switch_temp,
            wall_temp=wall_temp,
            ambient=ambient,
            delta_vs_wall=delta,
            max_temp=max_temp,
            min_temp=min_temp,
            avg_temp=avg_temp,
            hotspot=(hx, hy),
            heat_path=heat_path,
            trend_c_per_min=rate,
            trend_delta_c=trend_delta,
            rapid_increase=rapid,
            stability_c=stability,
            classification=cls,
            risk_score=risk,
            evidence=evidence,
            series=self._series,
            message=message,
            data_quality=self.last_quality.value if hasattr(self.last_quality, "value") else str(self.last_quality),
            quality_reasons=list(self.quality_reasons),
            emissivity=emissivity,
        )

    def unavailable(self) -> ThermalScanResult:
        """Return an honest 'no thermal data' result (never faked temperatures)."""
        return ThermalScanResult(thermal_available=False)


def _corner_reference_mean(temps: np.ndarray) -> float:
    """Mean of the four corner reference regions (15% of each dimension).

    Corners are the most likely place to find a uniform, un-heated surface to
    establish the ambient baseline without the switch's own heat.
    """
    h, w = temps.shape
    ch, cw = max(1, int(h * 0.15)), max(1, int(w * 0.15))
    corners = [
        temps[0:ch, 0:cw],
        temps[0:ch, w - cw : w],
        temps[h - ch : h, 0:cw],
        temps[h - ch : h, w - cw : w],
    ]
    vals = [float(np.nanmean(c)) for c in corners if c.size]
    return float(np.median(vals)) if vals else float(np.nanmean(temps))
