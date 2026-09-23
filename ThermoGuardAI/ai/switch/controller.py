"""Switch-first inspection controller (S1 state machine).

IDLE → CAMERA_INITIALIZING → SEARCHING_FOR_SWITCH → SWITCH_DETECTED →
SWITCH_STABLE → INSPECTION_REGION_LOCKED → THERMAL_INITIALIZING →
THERMAL_BASELINE → THERMAL_SCANNING → HOTSPOT_ANALYSIS →
THERMAL_TREND_ANALYSIS → ANOMALY_ANALYSIS → RISK_ASSESSMENT →
INSPECTION_COMPLETE

(CAMERA_INITIALIZING/IDLE live in the browser — the controller starts at
SEARCHING_FOR_SWITCH once the camera is streaming.)

The controller is pure logic (no I/O, no DB): it consumes RGB frames +
thermal frames and returns a serializable status dict. Persistence happens in
the service layer. Failures degrade to an ERROR state and retry on the next
frame — an exception can never destroy the inspection.
"""
from __future__ import annotations

import logging
from typing import Any

import cv2
import numpy as np

from ai.switch.guidance import evaluate_position
from ai.switch.roi import bbox_to_norm, expand_roi
from ai.switch.switch_detector import SwitchDetector
from ai.switch.thermal import SwitchThermalAnalyzer, ThermalScanResult
from backend.core.config import get_settings

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# States (the canonical S1 machine)
# ----------------------------------------------------------------------
SEARCHING_FOR_SWITCH = "SEARCHING_FOR_SWITCH"
SWITCH_DETECTED = "SWITCH_DETECTED"
SWITCH_STABLE = "SWITCH_STABLE"
INSPECTION_REGION_LOCKED = "INSPECTION_REGION_LOCKED"
THERMAL_INITIALIZING = "THERMAL_INITIALIZING"
THERMAL_BASELINE = "THERMAL_BASELINE"
THERMAL_SCANNING = "THERMAL_SCANNING"
HOTSPOT_ANALYSIS = "HOTSPOT_ANALYSIS"
THERMAL_TREND_ANALYSIS = "THERMAL_TREND_ANALYSIS"
ANOMALY_ANALYSIS = "ANOMALY_ANALYSIS"
RISK_ASSESSMENT = "RISK_ASSESSMENT"
INSPECTION_COMPLETE = "INSPECTION_COMPLETE"
ERROR = "ERROR"

_STATE_LABELS = {
    SEARCHING_FOR_SWITCH: "Looking for electrical switch…",
    SWITCH_DETECTED: "Switch detected — verifying position…",
    SWITCH_STABLE: "Switch confirmed ✓",
    INSPECTION_REGION_LOCKED: "Inspection region locked",
    THERMAL_INITIALIZING: "Thermal inspection starting…",
    THERMAL_BASELINE: "Stabilizing thermal sensor — collecting baseline…",
    THERMAL_SCANNING: "Thermal scanning — sampling temperature…",
    HOTSPOT_ANALYSIS: "Analyzing hotspot…",
    THERMAL_TREND_ANALYSIS: "Analyzing thermal trend…",
    ANOMALY_ANALYSIS: "Classifying anomaly…",
    RISK_ASSESSMENT: "Calculating risk…",
    INSPECTION_COMPLETE: "Inspection completed",
    ERROR: "Error — retrying…",
}

# Thermal demo heat (DEMO/SIMULATED only): guides the simulator so the switch
# shows a reproducible warm pattern while the whole flow is exercised. Real
# thermal hardware ignores this entirely.
_DEMO_SWITCH_DELTA_C = 28.0


class SwitchInspectionController:
    """Per-session state machine for the switch-first inspection."""

    def __init__(self, settings: Any | None = None, thermal_source: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self.detector = SwitchDetector(min_confidence=self.settings.switch_min_confidence)
        self.analyzer = SwitchThermalAnalyzer(
            baseline_frames=getattr(self.settings, "thermal_baseline_frames", None),
            scan_frames=getattr(self.settings, "thermal_scan_frames", None),
            elevated_delta_c=getattr(self.settings, "thermal_elevated_delta_c", None),
            abnormal_delta_c=getattr(self.settings, "thermal_abnormal_delta_c", None),
            critical_delta_c=getattr(self.settings, "thermal_critical_delta_c", None),
            rapid_rise_c_per_min=getattr(self.settings, "thermal_rapid_rise_c_per_min", None),
        )
        self.thermal_source = thermal_source  # injected (tests) or resolved lazily

        self.state = SEARCHING_FOR_SWITCH
        self.error: str | None = None
        self.frame_count = 0

        self.switch_bbox: tuple[int, int, int, int] | None = None
        self.switch_confidence = 0.0
        self.roi: tuple[int, int, int, int] | None = None
        self.stable_count = 0
        self.confirmed = False
        self.complete: ThermalScanResult | None = None
        self.guidance: dict | None = None

        self.thermal_available = False
        self.thermal_simulated = False
        self.thermal_source_name: str | None = None
        self._thermal_attempts = 0
        self._thermal_give_up_after = 10

    # ------------------------------------------------------------------
    @property
    def done(self) -> bool:
        return self.state == INSPECTION_COMPLETE

    def _thermal(self):
        """Resolve the thermal source (lazy so tests can inject later)."""
        if self.thermal_source is None:
            from ai.thermal.factory import get_thermal_source

            self.thermal_source = get_thermal_source()
        return self.thermal_source

    # ------------------------------------------------------------------
    def on_frame(self, frame: np.ndarray) -> dict:
        """Advance the state machine with one RGB frame. Returns a status dict."""
        self.frame_count += 1
        try:
            return self._step(frame)
        except Exception:  # noqa: BLE001 — never destroy the inspection
            logger.exception("Switch controller error on frame %d", self.frame_count)
            self.state = ERROR
            self.error = "Processing error — retrying"
            return self._status()

    def _step(self, frame: np.ndarray) -> dict:
        if self.done:
            return self._status()

        if self.confirmed and self.roi is not None:
            self._run_thermal(frame)
            return self._status()

        candidates = self.detector.detect(frame)
        best = candidates[0] if candidates else None

        if best is None:
            self.stable_count = 0
            self.state = SEARCHING_FOR_SWITCH
            self.guidance = None
            return self._status()

        self.switch_bbox = best.bbox
        self.switch_confidence = best.confidence
        self.guidance = evaluate_position(
            frame,
            best.bbox,
            size_min=self.settings.switch_size_min,
            size_max=self.settings.switch_size_max,
            center_tolerance=self.settings.switch_center_tolerance,
            min_brightness=self.settings.switch_min_brightness,
            min_sharpness=self.settings.switch_min_sharpness,
        ).to_dict()

        # A candidate is present this frame → at minimum SWITCH_DETECTED.
        self.state = SWITCH_DETECTED
        if self.guidance["ready"]:
            self.stable_count += 1
            if self.stable_count >= self.settings.switch_stable_frames:
                self._lock_inspection_region(frame)
        else:
            self.stable_count = 0
        return self._status()

    # ------------------------------------------------------------------
    def _lock_inspection_region(self, frame: np.ndarray) -> None:
        """Stable confirmation achieved → lock the ROI and start thermal."""
        self.confirmed = True
        self.state = SWITCH_STABLE
        if self.switch_bbox is not None:
            self.roi = expand_roi(self.switch_bbox, frame.shape, padding=self.settings.switch_roi_padding)
        self.state = INSPECTION_REGION_LOCKED
        self.state = THERMAL_INITIALIZING
        self._thermal()  # warm the source; sets simulated/available flags

    def _run_thermal(self, frame: np.ndarray) -> None:
        source = self._thermal()
        self.thermal_source_name = getattr(source, "name", "unknown")
        self.thermal_simulated = bool(getattr(source, "simulated", False))

        thermal = self._read_thermal(frame)
        if thermal is None:
            self._thermal_attempts += 1
            if self._thermal_attempts >= self._thermal_give_up_after:
                self._finish(self.analyzer.unavailable())
            return
        self.thermal_available = True
        self._thermal_attempts = 0

        switch_norm = None
        if self.switch_bbox is not None and self.roi is not None:
            switch_norm = bbox_to_norm(self.switch_bbox, frame.shape)
        stage_before = self.analyzer.stage
        # Record the ACTIVE source's emissivity (None = unavailable) so the
        # scan result always matches the sensor that produced the frames.
        final = self.analyzer.add(
            thermal,
            switch_norm,
            self.roi or (0, 0, frame.shape[1], frame.shape[0]),
            frame.shape,
            emissivity=getattr(source, "emissivity", None),
        )

        # Report the intermediate analysis states as the scan completes:
        if self.analyzer.stage == SwitchThermalAnalyzer.STAGE_SCANNING and stage_before != SwitchThermalAnalyzer.STAGE_SCANNING:
            self.state = THERMAL_SCANNING
        elif self.analyzer.stage == SwitchThermalAnalyzer.STAGE_BASELINE:
            self.state = THERMAL_BASELINE
        if final is not None:
            self._finish(final)

    def _read_thermal(self, frame: np.ndarray):
        """Read a thermal frame, guiding the DEMO simulator onto the switch ROI.

        Real hardware is read as-is. The simulator gets a hotspot centred on
        the locked ROI so the DEMO heat pattern is stable and reproducible.
        """
        source = self._thermal()
        if getattr(source, "name", "") == "simulator" and self.roi is not None:
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = self.roi
            cx = ((x1 + x2) / 2) / w
            cy = ((y1 + y2) / 2) / h
            return source.read(
                hotspots=[
                    {
                        "cx": cx,
                        "cy": cy,
                        "bbox": (x1 / w, y1 / h, x2 / w, y2 / h),
                        "delta": _DEMO_SWITCH_DELTA_C,
                        "spread": 1.15,
                    }
                ],
                blend_default=True,
            )
        return source.read()

    def _finish(self, result: ThermalScanResult) -> None:
        self.complete = result
        self.state = HOTSPOT_ANALYSIS
        # The remaining analysis stages complete on this frame; the payload
        # carries each stage's output so the UI shows the full breakdown.
        if result.thermal_available:
            self.state = THERMAL_TREND_ANALYSIS
            self.state = ANOMALY_ANALYSIS
            self.state = RISK_ASSESSMENT
        self.state = INSPECTION_COMPLETE

    # ------------------------------------------------------------------
    def _status(self) -> dict:
        scan_sample = self.analyzer._series[-1] if self.analyzer._series else None  # noqa: SLF001
        return {
            "state": self.state,
            "state_label": _STATE_LABELS.get(self.state, self.state),
            "message": self._message(),
            "error": self.error,
            "guidance": self.guidance,
            "switch_bbox": list(self.switch_bbox) if self.switch_bbox else None,
            "switch_confidence": round(self.switch_confidence, 3),
            "stable_frames": self.stable_count,
            "stable_required": self.settings.switch_stable_frames,
            "roi": list(self.roi) if self.roi else None,
            "thermal_available": self.thermal_available,
            "thermal_simulated": self.thermal_simulated,
            "thermal_source": self.thermal_source_name,
            "thermal_stage": self.analyzer.stage if self.thermal_available else None,
            # S4: authoritative frame quality + reasons (never inferred client-side)
            "thermal_quality": getattr(self.analyzer, "last_quality", "VALID"),
            "thermal_quality_reasons": list(getattr(self.analyzer, "quality_reasons", [])),
            "thermal_emissivity": getattr(self._thermal(), "emissivity", None),
            "thermal_metadata": self._thermal_metadata(),
            "baseline_frames": min(self.analyzer.baseline_frames, len(self.analyzer._baseline_means)) if self.analyzer.baseline_frames else 0,  # noqa: SLF001
            "baseline_required": self.analyzer.baseline_frames,
            "scan_frames": len(self.analyzer._series),  # noqa: SLF001
            "scan_required": self.analyzer.scan_frames,
            "scan_sample": scan_sample,
            "complete": self.complete.to_dict() if self.complete else None,
            "progress": self._progress(),
        }

    def _thermal_metadata(self) -> dict | None:
        """Sensor metadata as a plain dict (or None when unavailable)."""
        try:
            metadata = self._thermal().get_metadata()
            return metadata.to_dict() if hasattr(metadata, "to_dict") else {}
        except Exception:  # noqa: BLE001
            return None

    def _message(self) -> str:
        if self.error:
            return self.error
        if self.done:
            return self.complete.message if self.complete else "Inspection completed"
        if self.state == SEARCHING_FOR_SWITCH:
            return "Point the camera toward an electrical switch."
        if self.state in (SWITCH_DETECTED, SWITCH_STABLE) and self.guidance and self.guidance.get("messages"):
            return "; ".join(self.guidance["messages"])
        return _STATE_LABELS.get(self.state, self.state)

    def _progress(self) -> int:
        """Overall progress 0..100 across the whole state machine."""
        if self.done:
            return 100
        if self.confirmed:
            base = 55.0
            if self.analyzer.stage == SwitchThermalAnalyzer.STAGE_BASELINE:
                return int(base + 10 * min(1.0, len(self.analyzer._baseline_means) / max(1, self.analyzer.baseline_frames)))  # noqa: SLF001
            if self.analyzer.stage == SwitchThermalAnalyzer.STAGE_SCANNING:
                return int(base + 10 + 30 * min(1.0, len(self.analyzer._series) / max(1, self.analyzer.scan_frames)))  # noqa: SLF001
            return int(base)
        if self.switch_bbox is not None:
            return int(45 * min(1.0, self.stable_count / max(1, self.settings.switch_stable_frames)))
        return 5


def draw_switch_overlay(
    frame: np.ndarray,
    switch_bbox: tuple[int, int, int, int] | None,
    roi: tuple[int, int, int, int] | None,
    state_label: str,
    confidence: float | None = None,
    message: str | None = None,
) -> np.ndarray:
    """Annotate a frame with the switch box, ROI and state label (for transport)."""
    out = frame.copy()
    if roi is not None:
        x1, y1, x2, y2 = roi
        cv2.rectangle(out, (x1, y1), (x2, y2), (200, 200, 60), 1)
    if switch_bbox is not None:
        x1, y1, x2, y2 = switch_bbox
        color = (60, 220, 60)  # green = confirmed
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 3)
        label = "SWITCH"
        if confidence is not None:
            label += f" {confidence:.0%}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        cv2.rectangle(out, (x1, max(0, y1 - th - 10)), (x1 + tw + 6, y1), color, -1)
        cv2.putText(out, label, (x1 + 3, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (10, 40, 10), 1, cv2.LINE_AA)
    if message:
        cv2.putText(out, message[:70], (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(out, state_label[:60], (10, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (60, 220, 60), 1, cv2.LINE_AA)
    return out
