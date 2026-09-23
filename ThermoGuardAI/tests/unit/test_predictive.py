"""Unit tests for the predictive maintenance engine."""
from __future__ import annotations

from ai.predictive.maintenance import PredictiveMaintenanceEngine
from ai.predictive.rul import estimate_rul_hours, failure_probability
from ai.predictive.trend import linear_trend, smooth


def test_linear_trend_positive_slope() -> None:
    slope, intercept = linear_trend([0, 1, 2, 3], [10, 12, 14, 16])
    assert slope is not None and abs(slope - 2.0) < 1e-6
    assert intercept is not None


def test_linear_trend_insufficient_data() -> None:
    assert linear_trend([0], [5]) is None


def test_smooth_shortens_noise() -> None:
    raw = [10.0, 30.0, 10.0, 30.0]
    smoothed = smooth(raw, alpha=0.5)
    assert len(smoothed) == len(raw)
    assert abs(smoothed[-1] - raw[-1]) < 15.0


def test_rul_heating_trend() -> None:
    temps = [30.0, 36.0, 42.0, 48.0, 54.0]  # ~6°C per inspection
    rul = estimate_rul_hours(temps, limit_c=66.0, operating_hours_per_inspection=6.0)
    assert rul is not None
    # 12°C remaining at 6°C/inspection = 2 inspections * 6h = 12h
    assert abs(rul - 12.0) < 2.0


def test_rul_cooling_returns_none() -> None:
    temps = [50.0, 46.0, 42.0]
    assert estimate_rul_hours(temps, limit_c=60.0) is None


def test_failure_probability_rises_with_temp() -> None:
    low = failure_probability([25.0, 26.0, 27.0], limit_c=70.0)
    high = failure_probability([50.0, 60.0, 68.0], limit_c=70.0)
    assert low < 0.3
    assert high > 0.6


def test_predictive_engine_outputs() -> None:
    engine = PredictiveMaintenanceEngine()
    history = [
        {"label": "Breaker B2", "component_type": "circuit_breaker", "temperatures": [35.0, 45.0, 55.0, 64.0]},
        {"label": "Relay R3", "component_type": "relay", "temperatures": [30.0, 31.0, 30.5, 31.0]},
    ]
    predictions = engine.predict(history)
    assert len(predictions) == 2
    breaker = next(p for p in predictions if p.label == "Breaker B2")
    assert breaker.failure_probability > 0.5
    assert breaker.health in ("high", "critical")
    summary = engine.build_summary(predictions)
    assert "Breaker B2" in summary
