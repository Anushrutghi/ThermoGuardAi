"""S1 — switch thermal analysis unit tests."""
from __future__ import annotations

from ai.switch.roi import bbox_to_norm, expand_roi
from ai.switch.thermal import NORMAL, SwitchThermalAnalyzer
from tests.switch_test_utils import RampedThermalSource

H, W = 480, 640
SWITCH_BBOX = (260, 190, 380, 310)  # centred, ~120x120 px
ROI = expand_roi(SWITCH_BBOX, (H, W), padding=0.35)
SWITCH_NORM = bbox_to_norm(SWITCH_BBOX, (H, W))


def _feed(analyzer: SwitchThermalAnalyzer, source) -> object:  # noqa: ANN001
    """Feed baseline+scan frames and return the final result (or None)."""
    result = None
    for _ in range(analyzer.baseline_frames + analyzer.scan_frames + 2):
        result = analyzer.add(source.read(), SWITCH_NORM, ROI, (H, W))
        if result is not None:
            break
    return result


def test_analyzer_flags_hot_switch() -> None:
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    result = _feed(analyzer, RampedThermalSource(delta=30.0))
    assert result is not None
    assert result.thermal_available
    assert result.classification != NORMAL, "a clearly hot switch must not be NORMAL"
    assert result.switch_temp is not None and result.wall_temp is not None
    assert result.switch_temp > result.wall_temp
    assert result.delta_vs_wall is not None and result.delta_vs_wall > 0
    assert result.risk_score > 0
    assert len(result.evidence) >= 1
    assert len(result.series) == analyzer.scan_frames


def test_analyzer_normal_when_cool() -> None:
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    result = _feed(analyzer, RampedThermalSource(delta=1.5))
    assert result is not None
    assert result.classification == NORMAL
    assert result.risk_score <= 15
    assert result.delta_vs_wall is not None and result.delta_vs_wall < 10


def test_analyzer_requires_baseline_before_scan() -> None:
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    source = RampedThermalSource(delta=22.0)
    assert analyzer.stage == "baseline"
    analyzer.add(source.read(), SWITCH_NORM, ROI, (H, W))
    assert analyzer.stage == "baseline"
    analyzer.add(source.read(), SWITCH_NORM, ROI, (H, W))
    assert analyzer.stage == "scanning"
    assert analyzer.baseline_ready


def test_analyzer_rapid_increase_detected() -> None:
    """A sharply rising time-series must flag a rapid thermal increase."""
    analyzer = SwitchThermalAnalyzer(
        baseline_frames=2,
        scan_frames=4,
        rapid_rise_c_per_min=6.0,
    )
    result = _feed(analyzer, RampedThermalSource(delta=4.0, ramp=6.0))
    assert result is not None
    assert result.rapid_increase is True, "a steep rising series must be flagged"
    assert result.trend_c_per_min is not None and result.trend_c_per_min > 0
    assert any("Rapid thermal increase" in e for e in result.evidence)


def test_analyzer_stable_series_not_rapid() -> None:
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=4, rapid_rise_c_per_min=6.0)
    result = _feed(analyzer, RampedThermalSource(delta=22.0, ramp=0.0))
    assert result is not None
    assert result.rapid_increase is False
    assert abs(result.trend_c_per_min or 0.0) < 6.0
