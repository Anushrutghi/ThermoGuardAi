"""Unit tests for predictive engine statistical honesty, uncertainty bounds, and replacement isolation."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ai.predictive.maintenance import PredictiveMaintenanceEngine
from ai.predictive.rul import (
    FORECAST_ASSUMPTIONS,
    assess_failure_probability,
    project_threshold_crossing,
)
from ai.predictive.trend import analyze_trend


def test_insufficient_history_rejection() -> None:
    """Verifies that fewer than 5 observations or <48h span are rejected for trend projection."""
    # Case 1: 4 observations (under minimum of 5)
    t0 = datetime(2026, 3, 1, 10, 0)
    timestamps = [t0 + timedelta(hours=i * 24) for i in range(4)]
    temps = [30.0, 32.0, 34.0, 36.0]

    trend = analyze_trend(temps, timestamps=timestamps, min_observations=5)
    assert trend.status == "INSUFFICIENT_OBSERVATIONS"
    assert not trend.is_significant

    proj = project_threshold_crossing(temps, limit_c=70.0, timestamps=timestamps, min_observations=5)
    assert proj.status == "INSUFFICIENT_OBSERVATIONS"
    assert proj.projected_days is None

    # Case 2: 5 observations but over only 12 hours (under 48h minimum)
    timestamps_brief = [t0 + timedelta(hours=i * 2) for i in range(5)]
    temps_5 = [30.0, 32.0, 34.0, 36.0, 38.0]
    trend_brief = analyze_trend(temps_5, timestamps=timestamps_brief, min_duration_hours=48.0)
    assert trend_brief.status == "INSUFFICIENT_DURATION"
    assert not trend_brief.is_significant


def test_irregular_timestamps_and_confidence_intervals() -> None:
    """Verifies that irregular time-series correctly calculates slope/day, R², and 95% CI bounds."""
    t0 = datetime(2026, 3, 1, 10, 0)
    # Irregular observation schedule: Day 0, Day 1.5, Day 3, Day 5.2, Day 7.0 (Span = 7 days = 168h)
    days_offsets = [0.0, 1.5, 3.0, 5.2, 7.0]
    timestamps = [t0 + timedelta(days=d) for d in days_offsets]
    # Linear rise: 2.0 °C per day starting at 30.0 °C
    temps = [30.0 + 2.0 * d for d in days_offsets]

    trend = analyze_trend(temps, timestamps=timestamps, min_observations=5, min_duration_hours=48.0)
    assert trend.status == "VALID"
    assert trend.is_significant
    assert abs(trend.slope - 2.0) < 0.05
    assert trend.r_squared > 0.99
    assert trend.ci_95_lower > 1.8
    assert trend.ci_95_upper < 2.2

    # Project to 60 °C (from current 44.0 °C = 16.0 °C delta / 2.0 °C/day = ~8.0 days)
    proj = project_threshold_crossing(temps, limit_c=60.0, timestamps=timestamps)
    assert proj.status == "PROJECTED"
    assert proj.projected_days is not None
    assert abs(proj.projected_days - 8.0) < 0.5
    assert proj.lower_bound_days is not None
    assert proj.upper_bound_days is not None
    # Lower bound (fastest warming scenario) must be <= projected <= upper bound
    assert proj.lower_bound_days <= proj.projected_days <= proj.upper_bound_days
    assert len(proj.assumptions) >= 4


def test_cooling_and_noisy_variance_handling() -> None:
    """Verifies that cooling or high-noise trends are not presented as active thermal runaway."""
    t0 = datetime(2026, 3, 1, 10, 0)
    timestamps = [t0 + timedelta(days=i) for i in range(6)]

    # Cooling component
    cooling_temps = [50.0, 48.0, 46.0, 44.0, 42.0, 40.0]
    trend_cooling = analyze_trend(cooling_temps, timestamps=timestamps)
    assert trend_cooling.status == "FLAT_OR_COOLING"
    assert not trend_cooling.is_significant
    proj_cooling = project_threshold_crossing(cooling_temps, limit_c=70.0, timestamps=timestamps)
    assert proj_cooling.projected_days is None

    # Noisy oscillating readings (no structural heating trend)
    noisy_temps = [35.0, 42.0, 34.0, 43.0, 36.0, 41.0]
    trend_noisy = analyze_trend(noisy_temps, timestamps=timestamps)
    assert trend_noisy.status in ("INCONCLUSIVE_NOISE", "FLAT_OR_COOLING")
    assert not trend_noisy.is_significant
    proj_noisy = project_threshold_crossing(noisy_temps, limit_c=70.0, timestamps=timestamps)
    assert proj_noisy.projected_days is None


def test_ambient_context_conditioning() -> None:
    """Verifies that conditioning on ambient temperature removes false-positive weather heating."""
    t0 = datetime(2026, 3, 1, 10, 0)
    timestamps = [t0 + timedelta(days=i) for i in range(5)]

    # A heatwave causes ambient to surge from 15°C to 35°C over 4 days
    ambients = [15.0, 20.0, 25.0, 30.0, 35.0]
    # The component is completely healthy with constant rise-over-ambient ΔT = 10.0 °C
    component_raw = [amb + 10.0 for amb in ambients]  # 25, 30, 35, 40, 45 °C

    engine = PredictiveMaintenanceEngine(min_observations=5, min_duration_hours=48.0)

    # 1. Without ambient context, raw temperature falsely appears to be running away (+5 °C / day!)
    unconditioned_history = [
        {
            "label": "Breaker 1",
            "component_type": "circuit_breaker",
            "temperatures": component_raw,
            "timestamps": timestamps,
        }
    ]
    pred_unconditioned = engine.predict(unconditioned_history)[0]
    assert pred_unconditioned.trend_c_per_inspection > 4.0

    # 2. With ambient context, engine evaluates ΔT (which has slope 0.0), correctly identifying component is stable
    conditioned_history = [
        {
            "label": "Breaker 1",
            "component_type": "circuit_breaker",
            "temperatures": component_raw,
            "timestamps": timestamps,
            "ambients": ambients,
        }
    ]
    pred_conditioned = engine.predict(conditioned_history)[0]
    assert pred_conditioned.operating_context["conditioned_on_ambient"] is True
    assert pred_conditioned.trend_c_per_inspection == 0.0
    assert pred_conditioned.data_status == "FLAT_OR_COOLING"
    assert pred_conditioned.projected_crossing_days is None


def test_asset_replacement_isolation() -> None:
    """Verifies that observations prior to last_replaced_at are discarded and cannot leak into new asset."""
    t0 = datetime(2026, 3, 1, 10, 0)
    # 7 observations over 7 days
    timestamps = [t0 + timedelta(days=i) for i in range(7)]
    # Old component was severely overheated on days 0-3 (65, 70, 75, 80 °C)
    # Asset replaced on Day 4 (2026-03-05).
    # New healthy component installed on Day 4: days 4-6 (30, 30.5, 31 °C)
    temps = [65.0, 70.0, 75.0, 80.0, 30.0, 30.5, 31.0]

    engine = PredictiveMaintenanceEngine(min_observations=5, min_duration_hours=48.0)

    # With replacement date = 2026-03-05, days 0-3 must be purged
    history = [
        {
            "label": "Contactor K1",
            "component_type": "contactor",
            "temperatures": temps,
            "timestamps": timestamps,
            "last_replaced_at": datetime(2026, 3, 5, 0, 0),
        }
    ]
    preds = engine.predict(history)
    pred = preds[0]

    # Only 3 observations remain post-replacement (days 4, 5, 6), which is below min 5
    assert len(pred.temperatures) == 3
    assert pred.data_status == "INSUFFICIENT_OBSERVATIONS"
    # Old 80 °C temperature must not trigger a critical alarm on the new hardware
    assert pred.health != "critical"


def test_honest_probability_and_assumptions() -> None:
    """Verifies that failure probability is explicitly marked uncalibrated with assumptions."""
    assessment = assess_failure_probability([30.0, 35.0, 40.0], limit_c=70.0)
    assert assessment.calibration_status == "UNCALIBRATED_EXPERIMENTAL"
    assert assessment.confidence_level == "LOW"
    assert "uncalibrated" in assessment.explanation.lower()

    engine = PredictiveMaintenanceEngine()
    history = [
        {"label": "Relay 1", "component_type": "relay", "temperatures": [28.0, 29.0]},
    ]
    preds = engine.predict(history)
    assert preds[0].probability_status == "UNCALIBRATED_EXPERIMENTAL"
    assert len(preds[0].assumptions) >= 4
    for required in ("Constant electrical operating load", "Ambient temperature remains stable"):
        assert any(required in a for a in preds[0].assumptions)
