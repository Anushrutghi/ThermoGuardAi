"""Fault classifier — combines deep thermal analysis and visual findings."""
from __future__ import annotations

import logging

from ai.detector.base import Detection, HealthStatus
from ai.fault.rules import classify_delta, threshold_for
from ai.fault.types import Fault
from ai.thermal.base import ThermalFrame
from ai.thermal.processing import analyze_circuit_heat
from ai.visual.fault_heuristics import VisualInspectionResult
from backend.core.config import get_settings

logger = logging.getLogger(__name__)

FAULT_MESSAGES: dict[HealthStatus, str] = {
    HealthStatus.HEALTHY: "Component within normal temperature range.",
    HealthStatus.WARNING: "Temperature above normal operating range. Monitor closely.",
    HealthStatus.HIGH_RISK: "High temperature rise detected. Schedule inspection.",
    HealthStatus.CRITICAL: "Temperature exceeds safe operating limits. Immediate action required.",
}

RECOMMENDATIONS: dict[str, str] = {
    "loose_connection": "De-energize, torque terminals to spec, verify contact pressure.",
    "overheated_breaker": "Replace breaker after confirming load current within rating.",
    "overloaded_circuit": "Measure load current; redistribute circuits or upsize protection.",
    "high_resistance": "Inspect and clean contacts; check for oxidation or corrosion.",
    "short_circuit": "Isolate circuit immediately, test downstream wiring before re-energizing.",
    "phase_imbalance": "Measure phase currents; check for single-phase loads or faulty connections.",
    "overheating": "Cooling/ventilation check; reduce load; inspect terminals for oxidation.",
    "burn_mark": "Replace affected components; inspect for arcing source.",
    "visible_spark": "De-energize immediately and investigate arcing source.",
    "water_intrusion": "Seal enclosure, dry interior, check IP rating and cable glands.",
    "discoloration": "Inspect insulation integrity; schedule thermal re-test.",
    "loose_wire": "Tighten terminations and re-test continuity.",
    "smoke_haze": "De-energize, evacuate area, inspect for burning insulation.",
}


class FaultClassifier:
    """Builds a fault list from detections + thermal frame + visual findings."""

    def __init__(self, ambient: float | None = None) -> None:
        self.ambient = ambient

    def classify(
        self,
        detections: list[Detection],
        thermal: ThermalFrame | None,
        visual: VisualInspectionResult | None = None,
    ) -> list[Fault]:
        faults: list[Fault] = []
        ambient = self.ambient
        if thermal is not None:
            ambient = ambient if ambient is not None else float(thermal.mean_temp)

        required = get_settings().heat_confirm_frames

        for det in detections:
            if thermal is None:
                continue
            h, w = thermal.shape
            if h == 0 or w == 0:
                continue
            x1, y1, x2, y2 = det.bbox
            # bbox coordinates are in frame pixel space; the caller resizes the
            # thermal frame to match the RGB frame, so normalize against it.
            norm = (x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h)

            # Deep scan: core, borders, walls/wires and background surface.
            # Nothing is ever detected "directly" from a single point — the
            # whole circuit + its surroundings are analyzed for heat flow.
            _, _, crit = threshold_for(det.label)
            heat = analyze_circuit_heat(thermal, norm, ambient, critical_delta=crit)
            det.heat = heat

            temp = heat.core_center if heat.core_center > 0 else heat.core_max
            if temp == 0:
                continue
            det.temperature = temp
            delta = temp - ambient
            label = det.label.replace("_", " ").title()

            if heat.overload_candidate:
                # Heat is flowing outward (core → borders → wires), but we do
                # NOT alarm yet — the pipeline confirms it across many frames
                # before reporting a real OVERLOAD (no direct detection).
                det.health = HealthStatus.HIGH_RISK
                faults.append(
                    Fault(
                        fault_type="heat_verifying",
                        severity=HealthStatus.HIGH_RISK,
                        confidence=det.confidence,
                        component_label=det.label,
                        temperature=temp,
                        message=(
                            f"🔥 {label} heat detected — deep scan shows the circuit heating: "
                            f"core {heat.core_max:.1f} °C, borders {heat.border_mean:.1f} °C, "
                            f"heat flowing into surrounding wires ({heat.wall_mean:.1f} °C). "
                            f"Verifying over {required} consecutive frames before raising the alarm…"
                        ),
                        recommendation=RECOMMENDATIONS.get("overloaded_circuit", ""),
                    )
                )
            else:
                severity = classify_delta(delta, det.label)
                # A single reading can never be CRITICAL — confirmed overloads
                # come only from the multi-frame deep heat verification.
                if severity == HealthStatus.CRITICAL:
                    severity = HealthStatus.HIGH_RISK
                det.health = severity
                warn, high, _ = threshold_for(det.label)
                faults.append(
                    Fault(
                        fault_type="overheating",
                        severity=severity,
                        confidence=det.confidence,
                        component_label=det.label,
                        temperature=temp,
                        message=(
                            f"{label} at {temp:.1f} °C "
                            f"({delta:+.1f} °C above ambient; limits {warn:.0f}/{high:.0f}/{crit:.0f} °C). "
                            + FAULT_MESSAGES[severity]
                        ),
                        recommendation=RECOMMENDATIONS.get("overheating", ""),
                    )
                )

            if heat.moisture_candidate:
                # Article principle: moisture/leaks draw heat away — the
                # surface around the circuit is COLDER than its uniform
                # background, revealing hidden water behind the surface.
                faults.append(
                    Fault(
                        fault_type="water_intrusion",
                        severity=HealthStatus.HIGH_RISK,
                        confidence=0.7,
                        component_label=det.label,
                        message=(
                            f"💧 Moisture signature near {label} — surrounding surface "
                            f"is {heat.cold_anomaly:.1f} °C colder than the uniform background. "
                            "Possible water ingress or coolant leak behind the surface."
                        ),
                        recommendation=RECOMMENDATIONS.get("water_intrusion", ""),
                    )
                )

        if visual:
            for finding in visual.findings:
                faults.append(
                    Fault(
                        fault_type=finding.fault_type,
                        severity=finding.severity,
                        confidence=finding.confidence,
                        message=finding.message,
                        recommendation=RECOMMENDATIONS.get(finding.fault_type, "Investigate and rectify."),
                    )
                )

        return faults

