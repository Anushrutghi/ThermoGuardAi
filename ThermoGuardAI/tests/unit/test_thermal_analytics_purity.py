"""S4 final hardening — strict NaN/Infinity rejection from thermal analytics.

Proves the single authoritative validation path: NO NaN, +Infinity or
-Infinity may ever enter baseline calculation, time-series analysis, hotspot
calculations, thermal statistics, trend calculations, anomaly/risk analysis or
persisted history.

Policy under test (documented in docs/THERMAL_HARDWARE.md §4):
  * a frame is INVALID (fully excluded) when it has no usable pixels, when any
    value is outside the thermographic range, or when the non-finite fraction
    exceeds THERMAL_MAX_INVALID_FRACTION (5%);
  * sparse non-finite pixels (≤ threshold) are LOW_CONFIDENCE and are MASKED
    with the frame's own finite median BEFORE any statistic is computed;
  * masked analytics therefore always operate on finite-only data.
"""
from __future__ import annotations

import numpy as np

from ai.switch.roi import bbox_to_norm, expand_roi
from ai.switch.thermal import SwitchThermalAnalyzer
from ai.thermal.base import ThermalDataQuality, ThermalFrame
from tests.switch_test_utils import RampedThermalSource

H, W = 480, 640
SWITCH_BBOX = (260, 190, 380, 310)
ROI = expand_roi(SWITCH_BBOX, (H, W), padding=0.35)
SWITCH_NORM = bbox_to_norm(SWITCH_BBOX, (H, W))


def _frame(temps: np.ndarray) -> ThermalFrame:
    return ThermalFrame(temperatures=np.asarray(temps, dtype=np.float32), source="purity-test")


def _feed(analyzer: SwitchThermalAnalyzer, source) -> object:  # noqa: ANN001
    result = None
    for _ in range(analyzer.baseline_frames + analyzer.scan_frames + 3):
        result = analyzer.add(source.read(), SWITCH_NORM, ROI, (H, W))
        if result is not None:
            break
    return result


def _assert_all_finite(result) -> None:  # noqa: ANN001
    """Every analytics output of the result must be finite (no NaN/Inf)."""
    for key in ("switch_temp", "wall_temp", "ambient", "delta_vs_wall", "max_temp", "min_temp", "avg_temp", "trend_c_per_min", "trend_delta_c", "stability_c"):
        value = getattr(result, key)
        assert value is None or np.isfinite(value), f"{key} is not finite: {value!r}"
    assert np.isfinite(result.risk_score)
    if result.hotspot is not None:
        assert all(np.isfinite(v) for v in result.hotspot)
    for point in result.series:
        assert np.isfinite(point["switch"]) and np.isfinite(point["wall"]), f"series point not finite: {point}"


class CorruptingSource(RampedThermalSource):
    """A RampedThermalSource that injects NaN/±Inf into selected frames.

    `inject` maps absolute read-index → (row_frac, col_frac, value). The value
    is planted at a single pixel (sparse → LOW_CONFIDENCE path) unless
    `full_frame` is set, in which case the WHOLE frame becomes that value
    (→ INVALID path). Reads are relative to `start` so callers can corrupt
    specific baseline or scan positions.
    """

    def __init__(self, *args, inject=None, full_frame=False, **kwargs):  # noqa: ANN002
        super().__init__(*args, **kwargs)
        self.inject = inject or {}
        self.full_frame = full_frame

    def read(self, hotspots=None, blend_default=False):  # noqa: ANN001
        frame = super().read(hotspots=hotspots, blend_default=blend_default)
        idx = self._reads - 1  # already incremented by super().read()
        value = self.inject.get(idx)
        if value is not None:
            temps = frame.temperatures.copy()
            if self.full_frame:
                # value is a (row, col, scalar) tuple; use the scalar for the whole array
                scalar = value[2] if isinstance(value, tuple) else value
                temps[...] = scalar
            else:
                h, w = temps.shape
                temps[int(value[0] * h) % h, int(value[1] * w) % w] = value[2]
            return ThermalFrame(temperatures=temps, timestamp=frame.timestamp, source=frame.source)
        return frame


# ---------------------------------------------------------------------------
# 1) NaN / +Inf / -Inf frames — fully-invalid arrays are excluded entirely
# ---------------------------------------------------------------------------
def test_full_nan_frames_excluded() -> None:
    """A frame with NO usable pixels is INVALID and never enters analytics."""
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    result = None
    for _ in range(10):
        temps = np.full((24, 32), np.nan, dtype=np.float32)
        result = analyzer.add(_frame(temps), SWITCH_NORM, ROI, (H, W))
        assert result is None, "fully-NaN frames must be discarded, never analyzed"
    assert analyzer.last_quality == ThermalDataQuality.INVALID
    assert len(analyzer._baseline_means) == 0  # noqa: SLF001 — NaN never enters baseline
    assert len(analyzer._series) == 0  # noqa: SLF001


def test_full_pos_inf_frames_excluded() -> None:
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    for _ in range(10):
        temps = np.full((24, 32), np.inf, dtype=np.float32)
        assert analyzer.add(_frame(temps), SWITCH_NORM, ROI, (H, W)) is None
    assert analyzer.last_quality == ThermalDataQuality.INVALID


def test_full_neg_inf_frames_excluded() -> None:
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    for _ in range(10):
        temps = np.full((24, 32), -np.inf, dtype=np.float32)
        assert analyzer.add(_frame(temps), SWITCH_NORM, ROI, (H, W)) is None
    assert analyzer.last_quality == ThermalDataQuality.INVALID


# ---------------------------------------------------------------------------
# 2) Mixed valid/NaN frames — sparse invalid pixels are masked, analytics stay finite
# ---------------------------------------------------------------------------
def test_mixed_valid_nan_frame_keeps_analytics_finite() -> None:
    """One NaN pixel in a hot frame → LOW_CONFIDENCE, masked, result still finite."""
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    # corrupt the FINAL scan frame (index 4) so last_quality reflects it at finalize
    source = CorruptingSource(delta=30.0, inject={4: (0.5, 0.5, np.nan)})
    result = _feed(analyzer, source)
    assert result is not None, "a single corrupted frame must not kill the scan"
    assert result.data_quality == ThermalDataQuality.LOW_CONFIDENCE.value
    _assert_all_finite(result)
    assert result.classification != "UNAVAILABLE"


def test_mixed_inf_pixel_masked() -> None:
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    source = CorruptingSource(delta=30.0, inject={4: (0.2, 0.8, np.inf)})
    result = _feed(analyzer, source)
    assert result is not None
    _assert_all_finite(result)


def test_mixed_neg_inf_pixel_masked() -> None:
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    source = CorruptingSource(delta=30.0, inject={4: (0.8, 0.1, -np.inf)})
    result = _feed(analyzer, source)
    assert result is not None
    _assert_all_finite(result)


# ---------------------------------------------------------------------------
# 3) Invalid values in the hotspot region
# ---------------------------------------------------------------------------
def test_invalid_values_in_hotspot_region() -> None:
    """NaN planted exactly where the hotspot max lives → masked, hotspot finite."""
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    base = RampedThermalSource(delta=30.0)
    # find the hottest pixel of a real frame (the future hotspot location)
    probe = base.read()
    hot = np.unravel_index(int(np.nanargmax(probe.temperatures)), probe.temperatures.shape)
    source = CorruptingSource(delta=30.0, inject={4: (hot[0] / 24.0, hot[1] / 32.0, np.nan)})
    result = _feed(analyzer, source)
    assert result is not None
    assert result.hotspot is not None and np.isfinite(result.hotspot[0]) and np.isfinite(result.hotspot[1])
    _assert_all_finite(result)


# ---------------------------------------------------------------------------
# 4) Invalid frame between valid frames
# ---------------------------------------------------------------------------
def test_invalid_frame_between_valid_frames() -> None:
    """A fully-invalid frame mid-scan is skipped; the series stays finite."""
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    # corrupt scan frame index 4 (after baseline of 2 valid frames + 2 series)
    source = CorruptingSource(delta=30.0, inject={4: (0, 0, np.nan)}, full_frame=True)
    result = _feed(analyzer, source)
    assert result is not None, "scan must complete using the remaining valid frames"
    assert len(result.series) == analyzer.scan_frames, "invalid frame must not occupy a series slot"
    _assert_all_finite(result)


# ---------------------------------------------------------------------------
# 5) Completely invalid thermal sequence
# ---------------------------------------------------------------------------
def test_completely_invalid_sequence_never_analyzed() -> None:
    """No valid frames → no result is ever produced from invalid data."""
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    source = CorruptingSource(delta=30.0, full_frame=True, inject={i: (0, 0, np.nan) for i in range(10)})
    result = _feed(analyzer, source)
    assert result is None, "analytics must never be generated from an invalid sequence"
    assert len(analyzer._baseline_means) == 0  # noqa: SLF001
    assert len(analyzer._series) == 0  # noqa: SLF001


# ---------------------------------------------------------------------------
# 6) Baseline with invalid frames
# ---------------------------------------------------------------------------
def test_baseline_with_invalid_frames_skips_them() -> None:
    """Invalid frames during the baseline are discarded — baseline needs valid frames only."""
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=3)
    # frames 0 and 2 are fully invalid; valid frames 1, 3 supply the baseline
    source = CorruptingSource(
        delta=22.0,
        full_frame=True,
        inject={0: (0, 0, np.nan), 2: (0, 0, np.nan)},
    )
    result = _feed(analyzer, source)
    assert result is not None
    assert len(analyzer._baseline_means) >= 2, "baseline must only count VALID frames"
    assert all(np.isfinite(b) for b in analyzer._baseline_means)  # noqa: SLF001
    _assert_all_finite(result)


# ---------------------------------------------------------------------------
# 7) Trend calculation with invalid frames
# ---------------------------------------------------------------------------
def test_trend_with_invalid_frames_is_finite() -> None:
    """A corrupt frame inside a rising series must not poison the trend math."""
    analyzer = SwitchThermalAnalyzer(baseline_frames=2, scan_frames=4, rapid_rise_c_per_min=6.0)
    source = CorruptingSource(delta=4.0, ramp=6.0, inject={3: (0.3, 0.3, np.nan)})
    result = _feed(analyzer, source)
    assert result is not None
    assert result.trend_c_per_min is not None and np.isfinite(result.trend_c_per_min)
    assert result.trend_delta_c is not None and np.isfinite(result.trend_delta_c)
    assert np.isfinite(result.stability_c)
    _assert_all_finite(result)
