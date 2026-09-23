"""Unit tests for the adaptive performance engine."""
from __future__ import annotations

import time

import numpy as np

from ai.adaptive import AdaptivePerfController
from ai.pipeline import InspectionPipeline


def test_controller_starts_at_full_resolution() -> None:
    ctrl = AdaptivePerfController(target_fps=60.0, min_fps=10.0, min_scale=0.35, max_scale=1.0, max_stride=8, tune_interval_s=0.05)
    assert ctrl.scale == 1.0
    assert ctrl.stride == 1
    assert ctrl.should_analyze()


def test_controller_lowers_scale_when_slow() -> None:
    ctrl = AdaptivePerfController(target_fps=60.0, min_fps=10.0, min_scale=0.35, max_scale=1.0, max_stride=8, tune_interval_s=0.01)
    # simulate a slow device: ~20ms/frame (50fps achievable) — below 60*0.85
    for _ in range(60):
        ctrl.update(inference_ms=20.0)
        time.sleep(0.001)
    assert ctrl.scale < 1.0, "scale should drop when the device can't hit target"


def test_controller_raises_stride_at_min_scale() -> None:
    ctrl = AdaptivePerfController(target_fps=60.0, min_fps=10.0, min_scale=0.5, max_scale=1.0, max_stride=4, tune_interval_s=0.01)
    # extremely slow: 100ms/frame → 10fps
    for _ in range(80):
        ctrl.update(inference_ms=100.0)
        time.sleep(0.001)
    assert ctrl.stride > 1, "stride should increase when scale hits the floor"
    assert ctrl.scale <= 0.5


def test_controller_recovers_scale_with_headroom() -> None:
    ctrl = AdaptivePerfController(target_fps=60.0, min_fps=10.0, min_scale=0.35, max_scale=1.0, max_stride=4, tune_interval_s=0.01)
    # start slow
    for _ in range(60):
        ctrl.update(inference_ms=80.0)
        time.sleep(0.001)
    assert ctrl.scale < 1.0
    # now fast — should recover toward max
    for _ in range(60):
        ctrl.update(inference_ms=3.0)
        time.sleep(0.001)
    assert ctrl.scale > 0.5


def test_grade_classification() -> None:
    ctrl = AdaptivePerfController(target_fps=60.0)
    ctrl._achieved_fps = 60.0
    ctrl.scale = 1.0
    ctrl.stride = 1
    assert ctrl._grade() == "ultra"
    ctrl._achieved_fps = 45.0  # 45 >= 60*0.7 → high
    assert ctrl._grade() == "high"
    ctrl._achieved_fps = 30.0  # 30 >= 60*0.45 → mid
    assert ctrl._grade() == "mid"
    ctrl._achieved_fps = 8.0
    assert ctrl._grade() == "low"


def test_pipeline_stride_skips_analysis_but_annotates() -> None:
    pipeline = InspectionPipeline(detector_mode="fallback", target_fps=60.0)
    # force stride to 3 so only every 3rd frame is analyzed
    pipeline.perf.stride = 3
    pipeline.perf.max_stride = 3

    frame = np.full((240, 320, 3), 60, dtype=np.uint8)
    frame[60:140, 40:140] = (30, 30, 35)
    frame[150:190, 200:260] = (240, 240, 240)

    results = [pipeline.process(frame) for _ in range(6)]
    analyzed = [r.analyzed for r in results]
    assert analyzed == [True, False, False, True, False, False], analyzed
    # every result still carries an annotated frame + telemetry
    for r in results:
        assert r.frame.shape == frame.shape
        assert r.perf is not None
        assert r.perf.frames_total >= 1
    # analysis counter only increments on analyzed frames
    assert results[-1].perf.frames_analyzed == 2  # frames 0 and 3 of 6
    # cached results are re-used on skipped frames
    assert results[0].risk_score == results[1].risk_score
    assert results[0].component_count == results[1].component_count


def test_pipeline_scaled_detection_remaps_bboxes() -> None:
    pipeline = InspectionPipeline(detector_mode="fallback", target_fps=60.0)
    pipeline.perf.scale = 0.5
    frame = np.full((240, 320, 3), 60, dtype=np.uint8)
    frame[60:140, 40:140] = (30, 30, 35)
    frame[150:190, 200:260] = (240, 240, 240)
    result = pipeline.process(frame)
    for det in result.detections:
        x1, y1, x2, y2 = det.bbox
        assert x1 < frame.shape[1] and y1 < frame.shape[0], "bbox must be remapped to full resolution"
        assert x2 <= frame.shape[1] and y2 <= frame.shape[0]
