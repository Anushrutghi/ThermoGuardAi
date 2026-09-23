"""Unit tests for RGB visual fault heuristics."""
from __future__ import annotations

import numpy as np

from ai.visual.fault_heuristics import VisualFaultDetector


def _frame_with_burn_mark() -> np.ndarray:
    frame = np.full((480, 640, 3), 180, dtype=np.uint8)
    # dark charred patch
    frame[200:260, 200:340] = (20, 20, 20)
    return frame


def test_burn_mark_detected() -> None:
    detector = VisualFaultDetector()
    result = detector.analyze(_frame_with_burn_mark())
    types = [f.fault_type for f in result.findings]
    assert "burn_mark" in types


def test_clean_frame_no_critical_findings() -> None:
    rng = np.random.default_rng(7)
    frame = rng.integers(120, 200, (480, 640, 3), dtype=np.uint8)
    detector = VisualFaultDetector()
    result = detector.analyze(frame)
    # a clean textured frame should not produce burn marks or sparks
    assert "burn_mark" not in [f.fault_type for f in result.findings]
    assert "visible_spark" not in [f.fault_type for f in result.findings]
