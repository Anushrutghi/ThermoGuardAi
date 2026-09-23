"""Unit tests for the CV fallback detector and pipeline."""
from __future__ import annotations

import cv2
import numpy as np

from ai.detector.base import HealthStatus
from ai.detector.fallback_detector import FallbackDetector
from ai.fault.types import Fault
from ai.pipeline import InspectionPipeline


def _synthetic_panel_frame() -> np.ndarray:
    """A dark panel-like frame with a few bright modules for the fallback detector."""
    frame = np.full((480, 640, 3), 40, dtype=np.uint8)
    # dark modules
    frame[60:200, 40:140] = (25, 25, 30)
    frame[60:200, 180:300] = (28, 28, 32)
    # bright hotspot
    frame[300:360, 400:480] = (230, 230, 230)
    return frame


def test_fallback_detector_finds_components() -> None:
    detector = FallbackDetector()
    detections = detector.detect(_synthetic_panel_frame())
    assert len(detections) >= 1
    assert all(d.confidence > 0.3 for d in detections)
    assert all(d.label for d in detections)


def test_pipeline_process_end_to_end() -> None:
    pipeline = InspectionPipeline(detector_mode="fallback", ambient=25.0)
    result = pipeline.process(_synthetic_panel_frame())
    assert result.component_count >= 0
    assert 0.0 <= result.risk_score <= 100.0
    assert result.frame.shape == _synthetic_panel_frame().shape
    payload = result.to_json()
    assert "detections" in payload
    assert "risk_score" in payload


def test_risk_computation() -> None:
    faults = [Fault(fault_type="x", severity=HealthStatus.CRITICAL, confidence=0.9)]
    risk = InspectionPipeline._compute_risk(faults, [])
    assert risk > 50.0
    assert InspectionPipeline._compute_risk([], []) == 0.0


def _real_panel_frame() -> np.ndarray:
    """Bright backplate with dark modules + one bright heating zone."""
    frame = np.full((480, 640, 3), 165, dtype=np.uint8)
    frame[60:200, 40:140] = (30, 30, 36)
    frame[60:200, 180:300] = (34, 34, 40)
    frame[300:360, 400:480] = (235, 235, 235)
    return frame


def _random_scene() -> np.ndarray:
    rng = np.random.default_rng(3)
    scene = np.full((480, 640, 3), 150, dtype=np.uint8)
    for _ in range(6):
        c = (int(rng.integers(20, 240)), int(rng.integers(20, 440)))
        v = int(rng.integers(40, 110))
        cv2.circle(scene, c, int(rng.integers(30, 90)), (v, v, v), -1)
    cv2.GaussianBlur(scene, (0, 0), 12, dst=scene)
    return scene


def test_90pct_similarity_gate_keeps_real_modules() -> None:
    detector = FallbackDetector(min_confidence=0.90)
    detections = detector.detect(_real_panel_frame())
    labels = [d.label for d in detections]
    assert "circuit_breaker" in labels or "busbar" in labels
    assert "cable" in labels
    assert all(d.confidence >= 0.90 for d in detections)


def test_90pct_similarity_gate_rejects_random_scenes() -> None:
    detector = FallbackDetector(min_confidence=0.90)
    assert detector.detect(_random_scene()) == []
    assert detector.detect(np.full((480, 640, 3), 30, dtype=np.uint8)) == []


def test_detection_temporal_confirmation_waits_N_frames() -> None:
    pipe = InspectionPipeline(detector_mode="fallback", ambient=25.0)
    pipe._detection_confirm_frames = 5
    frame = _real_panel_frame()
    shape = frame.shape[:2]

    from ai.detector.base import Detection

    det = Detection(label="busbar", confidence=0.95, bbox=(180, 60, 300, 200))
    for _ in range(4):
        assert pipe._confirm_detections([det], shape) == [], "not shown before N frames"
    kept = pipe._confirm_detections([det], shape)
    assert [d.label for d in kept] == ["busbar"], "shown only after N consecutive frames"


def test_detection_decays_when_it_disappears() -> None:
    pipe = InspectionPipeline(detector_mode="fallback", ambient=25.0)
    pipe._detection_confirm_frames = 3
    shape = (480, 640)

    from ai.detector.base import Detection

    det = Detection(label="mcb", confidence=0.95, bbox=(40, 60, 140, 200))
    for _ in range(3):
        pipe._confirm_detections([det], shape)
    assert pipe._shown_detections, "confirmed after 3 frames"

    # candidate disappears -> sighting decays below 0 and is dropped
    for _ in range(3):
        pipe._confirm_detections([], shape)
    assert not pipe._shown_detections
