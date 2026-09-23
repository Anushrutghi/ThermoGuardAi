"""S1 — positioning guidance unit tests."""
from __future__ import annotations

from ai.switch.guidance import evaluate_position
from tests.switch_test_utils import make_switch_frame


def _bbox(cx: int, cy: int, pw: int = 120, ph: int = 160) -> tuple[int, int, int, int]:
    return (cx - pw // 2, cy - ph // 2, cx + pw // 2, cy + ph // 2)


def test_guidance_ready_when_well_positioned() -> None:
    frame = make_switch_frame()
    g = evaluate_position(frame, _bbox(320, 240))
    assert g.ready is True, g.messages
    assert g.checks["distance"] and g.checks["centering"] and g.checks["lighting"]
    assert g.score >= 70


def test_guidance_move_closer() -> None:
    frame = make_switch_frame()
    # a tiny switch (small in frame) → move closer
    g = evaluate_position(frame, _bbox(320, 240, pw=40, ph=30), size_min=0.12)
    assert not g.checks["distance"]
    assert any("closer" in m for m in g.messages)


def test_guidance_move_farther() -> None:
    frame = make_switch_frame()
    g = evaluate_position(frame, _bbox(320, 240, pw=260, ph=320), size_max=0.45)
    assert not g.checks["distance"]
    assert any("farther" in m for m in g.messages)


def test_guidance_move_slightly_right() -> None:
    frame = make_switch_frame()
    # switch far left of frame centre → nudge right
    g = evaluate_position(frame, _bbox(90, 240), center_tolerance=0.30)
    assert not g.checks["centering"]
    assert any("right" in m for m in g.messages), g.messages


def test_guidance_partially_visible() -> None:
    frame = make_switch_frame()
    # switch box touching the left edge
    g = evaluate_position(frame, _bbox(30, 240), center_tolerance=0.5)
    assert not g.checks["centering"]
    assert any("partially visible" in m for m in g.messages)


def test_guidance_insufficient_lighting() -> None:
    frame = make_switch_frame(brightness=38, rocker=20, wall=25)
    g = evaluate_position(frame, _bbox(320, 240), min_brightness=45)
    assert not g.checks["lighting"]
    assert any("lighting" in m for m in g.messages)


def test_guidance_blurry() -> None:
    import cv2

    frame = make_switch_frame()
    blurry = cv2.GaussianBlur(frame, (31, 31), 0)
    g = evaluate_position(blurry, _bbox(320, 240), min_sharpness=60)
    assert not g.checks["sharpness"]
    assert any("steady" in m or "blurry" in m for m in g.messages)
