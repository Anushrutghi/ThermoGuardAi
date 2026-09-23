"""S1 — wall-switch detector unit tests."""
from __future__ import annotations

from ai.switch.switch_detector import SwitchDetector
from tests.switch_test_utils import make_plain_wall, make_switch_frame


def test_detector_finds_synthetic_switch() -> None:
    detector = SwitchDetector(min_confidence=0.45)
    dets = detector.detect(make_switch_frame())
    assert dets, "a clear synthetic switch should be detected"
    best = dets[0]
    assert best.confidence >= 0.45
    # the box should roughly cover the plate and be centred in the frame
    cx = (best.bbox[0] + best.bbox[2]) / 2 / 640
    cy = (best.bbox[1] + best.bbox[3]) / 2 / 480
    assert 0.4 <= cx <= 0.6
    assert 0.4 <= cy <= 0.6
    # plausible switch size in frame
    assert 0.1 <= best.size_fraction <= 0.55


def test_detector_rejects_plain_wall() -> None:
    detector = SwitchDetector(min_confidence=0.45)
    dets = detector.detect(make_plain_wall())
    assert dets == [], "a textureless wall must not produce a switch"


def test_detector_confidence_gate() -> None:
    """A gate stricter than the default must still accept a genuine switch and reject weak noise."""
    strict = SwitchDetector(min_confidence=0.70)
    assert strict.detect(make_switch_frame()), "a crisp synthetic switch should pass a strict gate"
    noisy = make_plain_wall(wall=110, noise_std=12.0)
    assert len(SwitchDetector(min_confidence=0.70).detect(noisy)) == 0


def test_detector_dark_plate_on_light_wall() -> None:
    """Dark plates (dark rocker plates on light walls) are found via edges."""
    detector = SwitchDetector(min_confidence=0.45)
    frame = make_switch_frame(brightness=60, rocker=35, wall=200)
    dets = detector.detect(frame)
    assert dets, "a dark plate on a light wall should be detected via edge rectangles"
