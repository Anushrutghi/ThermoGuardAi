"""Unit tests for fault classification and severity rules."""
from __future__ import annotations

import numpy as np

from ai.detector.base import Detection, HealthStatus
from ai.fault.classifier import FaultClassifier
from ai.fault.rules import classify_absolute, classify_delta, threshold_for
from ai.thermal.base import ThermalFrame
from ai.thermal.processing import analyze_circuit_heat


def test_classify_delta_thresholds() -> None:
    assert classify_delta(5.0, "circuit_breaker") == HealthStatus.HEALTHY
    assert classify_delta(12.0, "circuit_breaker") == HealthStatus.WARNING
    assert classify_delta(22.0, "circuit_breaker") == HealthStatus.HIGH_RISK
    assert classify_delta(35.0, "circuit_breaker") == HealthStatus.CRITICAL


def test_classify_absolute_uses_ambient() -> None:
    # 45°C with 25°C ambient = 20°C rise → high risk
    assert classify_absolute(45.0, 25.0, "terminal") == HealthStatus.HIGH_RISK
    # 45°C with 40°C ambient = 5°C rise → healthy
    assert classify_absolute(45.0, 40.0, "terminal") == HealthStatus.HEALTHY


def test_threshold_for_component_specific() -> None:
    warn, high, crit = threshold_for("terminal")
    assert warn < threshold_for("transformer")[0]


def _radiating_frame(size: int = 64) -> ThermalFrame:
    """A circuit that radiates heat outward (core → borders → surrounding)."""
    yy, xx = np.mgrid[0:size, 0:size]
    dist = (xx - 25.5) ** 2 + (yy - 25.5) ** 2
    temps = 25.0 + 60.0 * np.exp(-dist / (2 * 20.0**2))
    return ThermalFrame(temperatures=temps.astype(np.float32))


def test_deep_scan_recognizes_radiating_heat() -> None:
    heat = analyze_circuit_heat(_radiating_frame(), (0.15, 0.15, 0.5, 0.5), ambient=25.0, critical_delta=25.0)
    # heat must flow outward: hot core, hot borders, hot walls — never a single point
    assert heat.core_max > heat.border_mean > heat.wall_mean
    assert heat.core_rise >= 0.5 * 25.0
    assert heat.overload_candidate is True
    assert heat.score > 0.5


def test_deep_scan_ignores_cold_uniform_frame() -> None:
    frame = ThermalFrame(temperatures=np.full((64, 64), 25.0, dtype=np.float32))
    heat = analyze_circuit_heat(frame, (0.15, 0.15, 0.5, 0.5), ambient=25.0, critical_delta=25.0)
    assert heat.overload_candidate is False
    assert heat.core_rise == 0.0
    assert heat.score == 0.0


def test_classifier_holds_hot_circuit_in_verification() -> None:
    # A circuit radiating deep heat becomes an overload *candidate* only — the
    # alarm is never raised directly; the pipeline confirms it over 100 frames.
    thermal = _radiating_frame()
    det = Detection(label="circuit_breaker", confidence=0.9, bbox=(10, 10, 40, 40))
    classifier = FaultClassifier(ambient=25.0)
    faults = classifier.classify([det], thermal)

    assert any(f.fault_type == "heat_verifying" for f in faults)
    assert all(f.fault_type != "overloaded_circuit" for f in faults)
    assert det.temperature is not None and det.temperature > 25.0
    assert det.health == HealthStatus.HIGH_RISK
    assert det.heat is not None and det.heat.overload_candidate


def test_classifier_single_hot_spot_is_never_critical() -> None:
    # Article principle: no direct single-point detection. A lone hot pixel on
    # an otherwise cold circuit must NOT be called critical — it is downgraded.
    temps = np.full((64, 64), 25.0, dtype=np.float32)
    temps[22, 22] = 85.0
    temps[23, 23] = 85.0
    det = Detection(label="circuit_breaker", confidence=0.9, bbox=(10, 10, 40, 40))
    faults = FaultClassifier(ambient=25.0).classify([det], ThermalFrame(temperatures=temps))
    assert all(f.severity != HealthStatus.CRITICAL for f in faults)


def test_classifier_without_thermal_still_runs() -> None:
    det = Detection(label="fuse", confidence=0.8, bbox=(10, 10, 40, 40))
    faults = FaultClassifier(ambient=25.0).classify([det], None)
    # no temperature → no overheating fault
    assert all(f.fault_type != "overheating" for f in faults)
