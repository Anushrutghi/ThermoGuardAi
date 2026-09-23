"""Real-time inspection pipeline — orchestrates the full detection chain.

Camera frame → preprocessing → detection → thermal mapping → temperature
estimation → fault detection → risk analysis → predictive maintenance →
annotated frame output.

The pipeline is **adaptive**: an `AdaptivePerfController` tunes detection
resolution (scale) and analysis frequency (stride) so the live stream stays
smooth at the target FPS on any hardware. When analysis is skipped for a
frame (stride > 1), the latest cached results are re-used while the current
frame is still annotated and streamed.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from ai.adaptive import AdaptivePerfController, PerfTelemetry
from ai.detector.base import Detection, HealthStatus
from ai.detector.factory import get_detector
from ai.fault.classifier import FaultClassifier
from ai.fault.types import Fault
from ai.thermal.factory import get_thermal_source
from ai.thermal.processing import ThermalStats, compute_stats, overlay_on_rgb, resize_to_rgb, to_colormap
from ai.thermal.simulator import ThermalSimulator
from ai.visual.fault_heuristics import VisualFaultDetector
from backend.core.config import get_settings

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {HealthStatus.HEALTHY: 0, HealthStatus.WARNING: 1, HealthStatus.HIGH_RISK: 2, HealthStatus.CRITICAL: 3}
SEVERITY_COLORS = {
    HealthStatus.HEALTHY: (60, 180, 60),
    HealthStatus.WARNING: (0, 200, 255),
    HealthStatus.HIGH_RISK: (0, 140, 255),
    HealthStatus.CRITICAL: (0, 0, 255),
}


@dataclass
class InspectionResult:
    """Full output of one pipeline run on a single frame."""

    frame: np.ndarray  # annotated BGR frame
    thermal_overlay: np.ndarray | None = None  # thermal colormap blended on frame (lazy)
    detections: list[Detection] = field(default_factory=list)
    faults: list[Fault] = field(default_factory=list)
    thermal_stats: ThermalStats | None = None
    visual_findings: list = field(default_factory=list)
    risk_score: float = 0.0
    fps: float = 0.0
    inference_ms: float = 0.0
    component_count: int = 0
    thermal_available: bool = False
    thermal_source: str | None = None  # source name, e.g. 'simulator' | 'mlx90640'
    thermal_simulated: bool = False  # True = DEMO readings — UIs must label them
    analyzed: bool = True  # False = cached results reused this frame (stride skip)
    perf: PerfTelemetry | None = None
    alarm_events: list[dict] = field(default_factory=list)  # cooldown-filtered alarms
    heat_verification: dict = field(default_factory=dict)  # deep-heat confirm progress

    @property
    def worst_severity(self) -> HealthStatus:
        severities = [f.severity for f in self.faults] + [d.health for d in self.detections]
        return max(severities, key=lambda s: SEVERITY_ORDER[s], default=HealthStatus.HEALTHY)

    def to_json(self, frame_encoding: str = "base64") -> dict:
        """Serialize for WebSocket/HTTP transport."""
        return {
            "detections": [
                {
                    "label": d.label,
                    "confidence": round(d.confidence, 3),
                    "bbox": list(d.bbox),
                    "temperature": round(d.temperature, 2) if d.temperature is not None else None,
                    "health": d.health.value,
                    "heat": d.heat.to_json() if getattr(d, "heat", None) is not None else None,
                }
                for d in self.detections
            ],
            "faults": [
                {
                    "fault_type": f.fault_type,
                    "severity": f.severity.value,
                    "confidence": round(f.confidence, 3),
                    "component_label": f.component_label,
                    "temperature": round(f.temperature, 2) if f.temperature is not None else None,
                    "message": f.message,
                    "recommendation": f.recommendation,
                }
                for f in self.faults
            ],
            "thermal_stats": _stats_dict(self.thermal_stats),
            "risk_score": round(self.risk_score, 1),
            "fps": round(self.fps, 1),
            "inference_ms": round(self.inference_ms, 1),
            "component_count": self.component_count,
            "thermal_available": self.thermal_available,
            "thermal_source": self.thermal_source,
            "thermal_simulated": self.thermal_simulated,
            "worst_severity": self.worst_severity.value,
            "analyzed": self.analyzed,
            "perf": self.perf.to_json() if self.perf else None,
            "frame_encoding": frame_encoding,
            "alarm_events": self.alarm_events,
            "heat_verification": self.heat_verification,
        }


class InspectionPipeline:
    """Adaptive pipeline runner. One instance per worker is fine."""

    def __init__(
        self,
        detector_mode: str | None = None,
        ambient: float | None = None,
        target_fps: float | None = None,
    ) -> None:
        self.detector = get_detector(detector_mode)
        self.thermal = get_thermal_source()
        self.thermal_source = self.thermal.name
        self.thermal_simulated = bool(getattr(self.thermal, "simulated", False))
        self.visual = VisualFaultDetector()
        settings = get_settings()
        self.classifier = FaultClassifier(ambient=ambient if ambient is not None else settings.thermal_ambient_c)
        self.perf = AdaptivePerfController(target_fps=target_fps)
        self._prev_time = time.monotonic()
        self._ema_fps = 0.0
        # The pipeline is a process-wide singleton shared by WS connections and
        # the HTTP endpoint; a lock serializes inference + tuning so concurrent
        # sessions cannot corrupt scale/stride/cached state.
        self._lock = threading.Lock()
        # last detection-guided thermal frame (reused for lazy overlay rendering)
        self._last_thermal_rgb: np.ndarray | None = None
        # cached analysis results reused on stride-skipped frames
        self._cached: dict = {
            "detections": [],
            "faults": [],
            "thermal_stats": None,
            "risk_score": 0.0,
            "component_count": 0,
            "heat_verification": {},
        }
        # Deep heat verification: a circuit is only reported OVERLOADED after
        # `heat_confirm_frames` consecutive analyzed frames confirm the heat is
        # flowing outward (core → borders → walls). Never trust a single frame.
        self._heat_confirm_frames = get_settings().heat_confirm_frames
        self._heat_confirm: dict[str, int] = {}  # component key → consecutive confirmed frames
        self.heat_verification: dict = {}  # exposed progress for the UI
        # Detection confirmation: a candidate must appear in consecutive frames
        # before it is shown/analyzed — transient blobs and shadows never show.
        self._detection_confirm_frames = get_settings().detection_confirm_frames
        self._detection_sightings: dict[str, int] = {}
        self._shown_detections: set[str] = set()

    # ------------------------------------------------------------------
    def process(self, frame: np.ndarray, with_overlay: bool = False) -> InspectionResult:
        """Run the adaptive pipeline on one BGR frame.

        `with_overlay` lazily computes the thermal colormap blend (expensive;
        only requested by clients that display the thermal view).
        """
        with self._lock:  # serialize inference across concurrent sessions
            return self._process_locked(frame, with_overlay)

    def _process_locked(self, frame: np.ndarray, with_overlay: bool = False) -> InspectionResult:
        t0 = time.monotonic()
        frame = self._preprocess(frame)
        analyze = self.perf.should_analyze()

        if analyze:
            result = self._analyze(frame)
            self._cached = {
                "detections": result.detections,
                "faults": result.faults,
                "thermal_stats": result.thermal_stats,
                "risk_score": result.risk_score,
                "component_count": result.component_count,
                "heat_verification": result.heat_verification,
            }
            self.perf.frames_analyzed += 1
        else:
            detections = self._cached["detections"]
            faults = self._cached["faults"]
            thermal_stats = self._cached["thermal_stats"]
            risk = self._cached["risk_score"]
            component_count = self._cached["component_count"]
            heat_verification = self._cached.get("heat_verification", {})
            thermal = self.thermal.read()  # cheap read; stats re-used from cache
            result = InspectionResult(
                frame=frame.copy(),  # replaced by annotated frame below
                thermal_stats=thermal_stats,
                detections=detections,
                faults=faults,
                risk_score=risk,
                component_count=component_count,
                thermal_available=thermal is not None,
                thermal_source=self.thermal_source,
                thermal_simulated=self.thermal_simulated,
                analyzed=False,
                heat_verification=heat_verification,
            )

        inference_ms = (time.monotonic() - t0) * 1000
        result.inference_ms = inference_ms

        # annotate the *current* frame regardless of analysis stride
        annotated = self._annotate(frame, result.detections, result.faults)
        result.frame = annotated

        if with_overlay and result.thermal_stats is not None:
            result.thermal_overlay = self._build_overlay(frame)

        now = time.monotonic()
        dt = now - self._prev_time
        self._prev_time = now
        inst_fps = 1.0 / dt if dt > 0 else 0.0
        self._ema_fps = inst_fps if self._ema_fps == 0 else 0.85 * self._ema_fps + 0.15 * inst_fps
        result.fps = self._ema_fps

        result.perf = self.perf.update(inference_ms)
        return result

    # ------------------------------------------------------------------
    def _analyze(self, frame: np.ndarray) -> InspectionResult:
        """Full analysis: scaled detection + visual CV + thermal stats."""
        scale = self.perf.scale

        # Scaled detection — bboxes remapped back to full resolution.
        # The CV fallback detector and visual heuristics are the dominant CPU
        # cost; running them on a downscaled copy is the biggest FPS lever.
        if scale < 1.0 and (frame.shape[1] * scale) >= 160:
            small = cv2.resize(frame, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            detections = self.detector.detect(small)
            inv = 1.0 / scale
            detections = [
                Detection(
                    label=d.label,
                    confidence=d.confidence,
                    bbox=(int(d.bbox[0] * inv), int(d.bbox[1] * inv), int(d.bbox[2] * inv), int(d.bbox[3] * inv)),
                    class_id=d.class_id,
                )
                for d in detections
            ]
            visual_result = self.visual.analyze(small)
            for finding in visual_result.findings:
                x1, y1, x2, y2 = finding.bbox
                finding.bbox = (int(x1 * inv), int(y1 * inv), int(x2 * inv), int(y2 * inv))
        else:
            detections = self.detector.detect(frame)
            visual_result = self.visual.analyze(frame)

        # Temporal gate: only show components that persist across consecutive
        # frames. One-off blobs/shadows never reach the classifier or the UI.
        detections = self._confirm_detections(detections, frame.shape[:2])

        # Thermal read + stats (colormap overlay is computed lazily, not here)
        thermal = self._thermal_for(frame, detections)
        thermal_stats: ThermalStats | None = None
        thermal_for_classifier = None
        if thermal is not None:
            thermal_rgb = resize_to_rgb(thermal, frame.shape[:2])
            self._last_thermal_rgb = thermal_rgb
            thermal_stats = compute_stats(thermal_rgb)
            thermal_for_classifier = thermal_rgb

        faults = self.classifier.classify(detections, thermal_for_classifier, visual_result)
        faults = self._confirm_circuit_heat(faults, detections, frame.shape[:2])
        risk = self._compute_risk(faults, detections)

        return InspectionResult(
            frame=frame.copy(),
            detections=detections,
            faults=faults,
            thermal_stats=thermal_stats,
            visual_findings=visual_result.findings,
            risk_score=risk,
            component_count=len(detections),
            thermal_available=thermal is not None,
            thermal_source=self.thermal_source,
            thermal_simulated=self.thermal_simulated,
            analyzed=True,
            heat_verification=self.heat_verification,
        )

    def thermal_overlay_for(self, frame: np.ndarray) -> np.ndarray | None:
        """Return the thermal colormap overlay for a frame without a full re-analysis.

        Uses the detection-guided thermal frame cached by the most recent
        analysis — the caller avoids paying for a second pipeline run just to
        render the heat map (evidence capture previously double-inferred).
        """
        try:
            return self._build_overlay(frame)
        except Exception:  # noqa: BLE001
            logger.exception("Thermal overlay failed")
            return None

    def _build_overlay(self, frame: np.ndarray) -> np.ndarray:
        """Build the thermal colormap overlay (expensive — called lazily).

        Uses the same detection-guided thermal frame that produced the current
        analysis, so the heat map lines up exactly with the detected circuits.
        """
        if getattr(self, "_last_thermal_rgb", None) is not None:
            overlay = to_colormap(self._last_thermal_rgb)
            return overlay_on_rgb(frame.copy(), overlay)
        thermal = self.thermal.read()
        if thermal is None:
            return frame.copy()
        thermal_rgb = resize_to_rgb(thermal, frame.shape[:2])
        overlay = to_colormap(thermal_rgb)
        return overlay_on_rgb(frame.copy(), overlay)

    # ------------------------------------------------------------------
    def _thermal_for(self, frame: np.ndarray, detections: list[Detection]) -> object:
        """Read the thermal frame.

        With the simulator this guides the heat map so it sits exactly on the
        detected components — each component keeps a *stable* temperature (keyed
        by its label + position, never random), and one is consistently hot so
        the OVERLOAD warning is reproducible instead of flickering.
        """
        if isinstance(self.thermal, ThermalSimulator) and detections:
            hotspots = self._detection_hotspots(frame.shape[:2], detections)
            # Full-scene thermal: the whole camera view shows temperature
            # (gradient + wire runs + every corner), with detection heat on top —
            # so heat is never only visible in the middle of the frame.
            return self.thermal.read(hotspots=hotspots, blend_default=True)
        return self.thermal.read()

    @staticmethod
    def _detection_hotspots(shape: tuple[int, int], detections: list[Detection]) -> list[dict]:
        """Stable per-component heat regions (dict hotspots) in frame space.

        The largest detected component is deterministically the hot/overloaded
        one, so a clear OVERLOAD always shows up; the rest get stable hash-based
        temperatures (never random). The overloaded circuit uses `spread > 1`
        so the simulated heat physically flows out to its borders and into the
        surrounding wires — healthy circuits keep their heat contained.
        """
        fh, fw = shape
        if not detections or fw == 0 or fh == 0:
            return []
        largest = max(
            range(len(detections)),
            key=lambda i: (detections[i].bbox[2] - detections[i].bbox[0]) * (detections[i].bbox[3] - detections[i].bbox[1]),
        )
        hotspots: list[dict] = []
        for i, det in enumerate(detections):
            x1, y1, x2, y2 = det.bbox
            cx = (x1 + x2) / 2 / fw
            cy = (y1 + y2) / 2 / fh
            if i == largest:
                hotspots.append(
                    {
                        "cx": cx,
                        "cy": cy,
                        "bbox": (x1 / fw, y1 / fh, x2 / fw, y2 / fh),
                        "delta": 58.0,
                        "spread": 2.2,
                    }
                )
            else:
                hotspots.append(
                    {
                        "cx": cx,
                        "cy": cy,
                        "bbox": (x1 / fw, y1 / fh, x2 / fw, y2 / fh),
                        "delta": InspectionPipeline._stable_delta(det, cx, cy),
                        "spread": 0.9,
                    }
                )
        return hotspots

    @staticmethod
    def _stable_delta(det: Detection, cx: float, cy: float) -> float:
        """Temperature rise (°C above ambient) for a component — deterministic.

        Keyed by the component's label + its position, so the same physical
        circuit always reads the same temperature instead of randomly jumping.
        A minority of components are consistently hot (OVERLOAD candidate).
        """
        base = {
            "circuit_breaker": 12.0, "breaker": 12.0, "mcb": 12.0, "mccb": 12.0, "rccb": 12.0,
            "cable": 10.0, "terminal": 9.0, "fuse": 10.0, "busbar": 8.0,
            "relay": 8.0, "contactor": 8.0, "transformer": 15.0, "power_supply": 12.0,
            "motor_starter": 12.0, "disconnect_switch": 10.0,
        }.get(det.label, 8.0)
        key = f"{det.label}|{round(cx, 2)}|{round(cy, 2)}"
        roll = int(hashlib.md5(key.encode()).hexdigest(), 16) % 1000 / 1000.0
        if roll < 0.45:
            return base * 0.7  # healthy
        if roll < 0.75:
            return base * 1.3  # warning
        if roll < 0.9:
            return base * 2.0  # high risk
        return 58.0  # overloaded → critical

    # ------------------------------------------------------------------
    def _confirm_detections(
        self, detections: list[Detection], shape: tuple[int, int]
    ) -> list[Detection]:
        """Temporal confirmation for detections.

        A candidate must be seen at >=90% similarity for `detection_confirm_frames`
        consecutive frames before it is shown at all. Once shown, it stays visible
        while it keeps appearing; a disappearing candidate decays and is dropped.
        This is what stops random blobs/shadows from flashing up as "circuits".
        """
        required = self._detection_confirm_frames
        if required <= 1:
            return detections
        fh, fw = shape
        present: set[str] = set()
        kept: list[Detection] = []
        for det in detections:
            x1, y1, x2, y2 = det.bbox
            cx = round((x1 + x2) / 2 / max(1, fw), 2)
            cy = round((y1 + y2) / 2 / max(1, fh), 2)
            key = f"{det.label}|{cx}|{cy}"
            present.add(key)
            count = self._detection_sightings.get(key, 0) + 1
            self._detection_sightings[key] = count
            if count >= required or key in self._shown_detections:
                self._shown_detections.add(key)
                kept.append(det)
        for key in list(self._detection_sightings):
            if key not in present:
                self._detection_sightings[key] -= 2
                if self._detection_sightings[key] <= 0:
                    self._detection_sightings.pop(key, None)
                    self._shown_detections.discard(key)
        return kept

    # ------------------------------------------------------------------
    def _confirm_circuit_heat(
        self,
        faults: list[Fault],
        detections: list[Detection],
        shape: tuple[int, int],
    ) -> list[Fault]:
        """Deep heat verification — the "check it N times" gate.

        A circuit that fails the deep scan (hot core + hot borders + heat
        flowing into the surrounding wires) is held in a `heat_verifying`
        HIGH_RISK state. Only after `heat_confirm_frames` consecutive analyzed
        frames keep confirming it does the fault upgrade to a critical
        `overloaded_circuit` (which is what triggers the buzzer). Any frame
        where the heat is no longer confirmed resets the counter.
        """
        required = self._heat_confirm_frames
        fh, fw = shape
        seen: set[str] = set()
        progress: dict[str, dict] = {}
        for det in detections:
            heat = getattr(det, "heat", None)
            if heat is None or not heat.overload_candidate:
                continue
            x1, y1, x2, y2 = det.bbox
            cx = round((x1 + x2) / 2 / max(1, fw), 2)
            cy = round((y1 + y2) / 2 / max(1, fh), 2)
            key = f"{det.label}|{cx}|{cy}"
            count = min(required, self._heat_confirm.get(key, 0) + 1)
            self._heat_confirm[key] = count
            seen.add(key)
            progress[key] = {"component": det.label, "confirmed_frames": count, "required": required}
            if count >= required:
                self._upgrade_to_overload(det, faults)
        # any component not seen this frame stops confirming → reset
        for key in list(self._heat_confirm):
            if key not in seen:
                del self._heat_confirm[key]
        self.heat_verification = progress
        return faults

    @staticmethod
    def _upgrade_to_overload(det: Detection, faults: list[Fault]) -> None:
        """Turn a verified hot circuit into a critical OVERLOADED fault."""
        heat = det.heat
        label = det.label.replace("_", " ").title()
        det.health = HealthStatus.CRITICAL
        for f in faults:
            if f.component_label == det.label and f.fault_type == "heat_verifying":
                f.fault_type = "overloaded_circuit"
                f.severity = HealthStatus.CRITICAL
                f.temperature = det.temperature or heat.core_center
                f.message = (
                    f"⚠ CIRCUIT OVERLOADED — {label} confirmed overheated after deep heat verification "
                    f"({heat.core_max:.1f} °C core, borders {heat.border_mean:.1f} °C, "
                    f"heat flowing into wires {heat.wall_mean:.1f} °C · flow {heat.flow_outward:.1f} °C). "
                    "Immediate action required — likely overloading or a failing connection."
                )

    # ------------------------------------------------------------------
    @staticmethod
    def _preprocess(frame: np.ndarray) -> np.ndarray:
        if frame is None or frame.size == 0:
            raise ValueError("Empty frame passed to pipeline")
        if frame.dtype != np.uint8:
            frame = np.clip(frame, 0, 255).astype(np.uint8)
        return frame

    @staticmethod
    def _compute_risk(faults: list[Fault], detections: list[Detection]) -> float:
        """0–100 risk score from faults + worst detection health."""
        if not faults and not detections:
            return 0.0
        total = 0.0
        weights = {HealthStatus.HEALTHY: 0.0, HealthStatus.WARNING: 25.0, HealthStatus.HIGH_RISK: 60.0, HealthStatus.CRITICAL: 95.0}
        for f in faults:
            total += weights[f.severity] * f.confidence
        for d in detections:
            total += weights[d.health] * 0.4
        return float(min(100.0, total / max(1, len(faults) + len(detections))))

    @staticmethod
    def _annotate(frame: np.ndarray, detections: list[Detection], faults: list[Fault]) -> np.ndarray:
        """Draw bounding boxes, labels, temperatures and fault markers."""
        out = frame.copy()
        for det in detections:
            x1, y1, x2, y2 = det.bbox
            color = SEVERITY_COLORS[det.health]
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
            label = det.label.replace("_", " ").title()
            if det.health == HealthStatus.CRITICAL:
                label = "⚠ OVERLOADED " + label
            text = f"{label} {det.confidence:.0%}"
            if det.temperature is not None:
                text += f"  {det.temperature:.1f}°C"
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(out, (x1, y1 - th - 8), (x1 + tw + 6, y1), color, -1)
            cv2.putText(out, text, (x1 + 3, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        # flashing red border on critical
        if any(f.severity == HealthStatus.CRITICAL for f in faults):
            if int(time.time() * 2) % 2 == 0:
                cv2.rectangle(out, (2, 2), (out.shape[1] - 3, out.shape[0] - 3), (0, 0, 255), 6)
        return out


def _stats_dict(stats: ThermalStats | None) -> dict | None:
    if stats is None:
        return None
    return {
        "max_temp": round(stats.max_temp, 2),
        "min_temp": round(stats.min_temp, 2),
        "avg_temp": round(stats.avg_temp, 2),
        "delta": round(stats.delta, 2),
        "hotspot_x": round(stats.hotspot_x, 3),
        "hotspot_y": round(stats.hotspot_y, 3),
        "gradient": round(stats.gradient, 2),
        "heat_spread": round(stats.heat_spread, 3),
    }


def encode_frame_jpeg(frame: np.ndarray, quality: int = 80, max_width: int | None = None) -> bytes:
    """Encode a BGR frame to JPEG bytes for transport.

    `max_width` downscales wide frames so low-end networks don't choke on
    full-resolution base64 payloads — the biggest bandwidth lever.
    """
    if max_width and frame.shape[1] > max_width:
        ratio = max_width / frame.shape[1]
        frame = cv2.resize(frame, (0, 0), fx=ratio, fy=ratio, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise ValueError("Frame encoding failed")
    return buf.tobytes()
