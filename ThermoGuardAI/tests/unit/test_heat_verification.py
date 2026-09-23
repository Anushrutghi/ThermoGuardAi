"""Tests for the deep heat verification gate (the "check it N times" rule).

An overloaded circuit must be confirmed across 100 consecutive analyzed
frames before it is reported as a critical OVERLOAD and can sound the buzzer.
A single hot frame must never trigger it.
"""
from __future__ import annotations

from ai.detector.base import Detection, HealthStatus
from ai.fault.types import Fault
from ai.pipeline import InspectionPipeline
from ai.thermal.processing import CircuitHeatAnalysis


def _candidate_detection() -> Detection:
    det = Detection(label="cable", confidence=0.92, bbox=(100, 100, 300, 300), health=HealthStatus.HIGH_RISK)
    det.heat = CircuitHeatAnalysis(
        core_max=80.0,
        core_center=79.0,
        border_mean=46.0,
        wall_mean=39.0,
        flow_outward=41.0,
        core_rise=55.0,
        border_rise=21.0,
        wall_rise=14.0,
        overload_candidate=True,
    )
    return det


def _verifying_fault(det: Detection) -> Fault:
    return Fault(
        fault_type="heat_verifying",
        severity=HealthStatus.HIGH_RISK,
        confidence=det.confidence,
        component_label=det.label,
        temperature=det.heat.core_center,
    )


def test_no_alarm_before_100_consecutive_frames() -> None:
    pipe = InspectionPipeline()
    det = _candidate_detection()
    pipe._heat_confirm_frames = 100

    confirmed = None
    for _ in range(99):
        faults = pipe._confirm_circuit_heat([_verifying_fault(det)], [det], (480, 640))
        if any(f.fault_type == "overloaded_circuit" for f in faults):
            confirmed = faults
            break

    assert confirmed is None, "must NOT confirm before 100 frames"
    assert det.health == HealthStatus.HIGH_RISK


def test_confirmed_after_100_consecutive_frames() -> None:
    pipe = InspectionPipeline()
    det = _candidate_detection()
    pipe._heat_confirm_frames = 100

    faults = None
    for _ in range(100):
        faults = pipe._confirm_circuit_heat([_verifying_fault(det)], [det], (480, 640))

    assert any(f.fault_type == "overloaded_circuit" and f.severity == HealthStatus.CRITICAL for f in faults)
    assert det.health == HealthStatus.CRITICAL
    assert "heat flowing" in next(f.message for f in faults if f.fault_type == "overloaded_circuit")


def test_counter_resets_when_heat_not_confirmed() -> None:
    pipe = InspectionPipeline()
    det = _candidate_detection()
    cold = Detection(label="cable", confidence=0.92, bbox=(100, 100, 300, 300))
    cold.heat = CircuitHeatAnalysis(overload_candidate=False)
    pipe._heat_confirm_frames = 100

    for _ in range(50):
        pipe._confirm_circuit_heat([_verifying_fault(det)], [det], (480, 640))
    # heat disappears → counter must reset, no partial credit left
    pipe._confirm_circuit_heat([], [cold], (480, 640))

    # a fresh 100 frames are now required (50 + reset → back to 0)
    for _ in range(99):
        faults = pipe._confirm_circuit_heat([_verifying_fault(det)], [det], (480, 640))
    assert not any(f.fault_type == "overloaded_circuit" for f in faults), "99 fresh frames must not confirm"

    faults = pipe._confirm_circuit_heat([_verifying_fault(det)], [det], (480, 640))
    assert any(f.fault_type == "overloaded_circuit" for f in faults), "the 100th fresh frame confirms"
    assert pipe.heat_verification


def test_heat_verification_progress_exposed() -> None:
    pipe = InspectionPipeline()
    det = _candidate_detection()
    pipe._heat_confirm_frames = 100

    for _ in range(7):
        pipe._confirm_circuit_heat([_verifying_fault(det)], [det], (480, 640))

    assert len(pipe.heat_verification) == 1
    progress = next(iter(pipe.heat_verification.values()))
    assert progress["component"] == "cable"
    assert progress["confirmed_frames"] == 7
    assert progress["required"] == 100
