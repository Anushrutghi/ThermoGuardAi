"""S2 unit tests — trend analysis, health engine, risk engine, recommendations.

These exercise the pure engines directly with real, deterministic inputs.
"""
from __future__ import annotations

import numpy as np

from analytics.health_engine import HealthInputs, compute_health
from analytics.recommendations import maintenance_recommendation
from analytics.risk_engine import RiskInputs, compute_risk
from analytics.trends import (
    DECREASING,
    INCONSISTENT,
    INCREASING,
    RAPIDLY_INCREASING,
    SLIGHTLY_INCREASING,
    STABLE,
    classify_delta_trend,
    classify_temperature_trend,
)


# ---------------------------------------------------------------------------
# Temperature trend (§4)
# ---------------------------------------------------------------------------
def test_trend_insufficient_data() -> None:
    result = classify_temperature_trend([31.0, 34.0])
    assert result.insufficient is True
    assert result.status is None
    assert result.message == "Insufficient historical data for trend analysis."


def test_trend_stable() -> None:
    result = classify_temperature_trend([31.0, 32.0, 31.0, 32.0, 31.0])
    assert result.insufficient is False
    assert result.status == STABLE


def test_trend_slightly_increasing() -> None:
    result = classify_temperature_trend([31.0, 33.0, 34.0, 36.0, 37.0])
    assert result.status == SLIGHTLY_INCREASING


def test_trend_increasing() -> None:
    result = classify_temperature_trend([31.0, 33.0, 36.0, 38.0, 41.0])  # slope ≈ +2.5
    assert result.status == INCREASING


def test_trend_rapidly_increasing() -> None:
    # the spec example: 31 → 34 → 39 → 47 → 55
    result = classify_temperature_trend([31.0, 34.0, 39.0, 47.0, 55.0])
    assert result.status == RAPIDLY_INCREASING
    assert result.slope_c_per_inspection is not None and result.slope_c_per_inspection > 3.0


def test_trend_decreasing() -> None:
    result = classify_temperature_trend([55.0, 47.0, 39.0, 34.0, 31.0])
    assert result.status == DECREASING


# ---------------------------------------------------------------------------
# ΔT trend (§5)
# ---------------------------------------------------------------------------
def test_delta_trend_insufficient() -> None:
    result = classify_delta_trend([5.0, 6.0])
    assert result.insufficient is True


def test_delta_trend_increasing() -> None:
    result = classify_delta_trend([5.0, 8.0, 12.0, 16.0, 21.0])
    assert result.status == INCREASING


def test_delta_trend_decreasing() -> None:
    result = classify_delta_trend([21.0, 16.0, 12.0, 8.0, 5.0])
    assert result.status == DECREASING


def test_delta_trend_stable() -> None:
    result = classify_delta_trend([5.0, 5.2, 4.9, 5.1, 5.0])
    assert result.status == STABLE


def test_delta_trend_inconsistent() -> None:
    # swings meaningfully in both directions → not forced into a single trend
    result = classify_delta_trend([5.0, 18.0, 6.0, 20.0, 7.0])
    assert result.status == INCONSISTENT


# ---------------------------------------------------------------------------
# Device health engine (§7)
# ---------------------------------------------------------------------------
def test_health_insufficient() -> None:
    result = compute_health(HealthInputs(completed_inspections=1), min_inspections=2)
    assert result.insufficient is True
    assert result.message == "Insufficient data to calculate device health."
    assert result.score is None


def test_health_explainable_and_deterministic() -> None:
    inputs = HealthInputs(
        latest_classification="ABNORMAL",
        temperature_trend="INCREASING",
        latest_fault_severity="high",
        repeated_anomaly=True,
        completed_inspections=8,
    )
    first = compute_health(inputs)
    second = compute_health(inputs)
    assert first.insufficient is False
    assert first.score == second.score, "health must be deterministic"
    assert 0 <= first.score <= 100
    names = [c.name for c in first.contributors]
    for expected in ("Thermal condition", "Thermal trend", "Recent anomaly", "Repeated anomaly", "Inspection history"):
        assert expected in names, expected
    # an ABNORMAL+repeated device should be well below perfect
    assert first.score < 80


def test_health_good_device() -> None:
    result = compute_health(
        HealthInputs(latest_classification="NORMAL", temperature_trend="STABLE", repeated_anomaly=False, completed_inspections=6)
    )
    assert result.insufficient is False
    assert result.score >= 80
    assert result.rating == "GOOD"


def test_health_weights_are_configurable() -> None:
    inputs = HealthInputs(latest_classification="CRITICAL", completed_inspections=6)
    neutral = compute_health(inputs, weights={"thermal": 0.2, "trend": 0.2, "anomaly": 0.2, "repeated": 0.2, "history": 0.2})
    heavy = compute_health(inputs, weights={"thermal": 0.9, "trend": 0.025, "anomaly": 0.025, "repeated": 0.025, "history": 0.025})
    assert neutral.score > heavy.score, "a higher thermal weight must drag the score down for a CRITICAL condition"


# ---------------------------------------------------------------------------
# Risk engine (§8)
# ---------------------------------------------------------------------------
def test_risk_insufficient() -> None:
    result = compute_risk(RiskInputs(has_any_inspection=False))
    assert result.insufficient is True
    assert result.message == "Insufficient data to assess risk."


def test_risk_normal() -> None:
    result = compute_risk(
        RiskInputs(
            latest_classification="NORMAL",
            latest_delta=2.0,
            has_any_inspection=True,
        )
    )
    assert result.insufficient is False
    assert result.level == "NORMAL"
    assert result.evidence == []
    assert result.reasoning


def test_risk_abnormal_with_evidence() -> None:
    result = compute_risk(
        RiskInputs(
            latest_classification="ABNORMAL",
            latest_delta=18.0,
            heat_path="localized",
            temperature_trend="INCREASING",
            repeated_anomaly=True,
            has_any_inspection=True,
        )
    )
    assert result.level == "ABNORMAL"
    evidence = "\n".join(result.evidence).lower()
    assert "localized hotspot detected" in evidence
    assert "+18.0 °c relative to reference" in evidence
    assert "increasing historical temperature trend" in evidence
    assert "similar anomaly found in previous inspections" in evidence


def test_risk_escalates_on_worsening_trend() -> None:
    # latest scan ELEVATED but history is rapidly worsening → at least ABNORMAL
    result = compute_risk(
        RiskInputs(
            latest_classification="ELEVATED",
            latest_delta=12.0,
            temperature_trend="RAPIDLY_INCREASING",
            has_any_inspection=True,
        )
    )
    assert result.level in ("ABNORMAL", "CRITICAL")
    assert any("worsening" in e.lower() for e in result.evidence)


def test_risk_critical_when_classification_critical() -> None:
    result = compute_risk(
        RiskInputs(latest_classification="CRITICAL", latest_delta=40.0, has_any_inspection=True)
    )
    assert result.level == "CRITICAL"
    assert result.risk_score >= 80


# ---------------------------------------------------------------------------
# Maintenance recommendations (§9)
# ---------------------------------------------------------------------------
def test_recommendation_insufficient() -> None:
    result = maintenance_recommendation(risk_level=None)
    assert result.insufficient is True
    assert "Insufficient" in result.recommendation


def test_recommendation_normal() -> None:
    result = maintenance_recommendation(risk_level="NORMAL")
    assert result.recommendation == "Continue routine inspection."


def test_recommendation_repeated() -> None:
    result = maintenance_recommendation(risk_level="ELEVATED", repeated_anomaly=True)
    assert "Professional electrical inspection recommended." in result.recommendation


def test_recommendation_worsening_trend() -> None:
    result = maintenance_recommendation(risk_level="NORMAL", temperature_trend="INCREASING")
    assert "worsening" in result.recommendation.lower()
    assert "Schedule professional inspection." in result.recommendation


def test_recommendation_critical_safety_wording() -> None:
    result = maintenance_recommendation(risk_level="CRITICAL")
    assert "Avoid touching or opening the installation" in result.recommendation
    assert "qualified electrician" in result.recommendation
    # never claims a confirmed electrical fault
    assert "confirmed electrical fault" not in result.recommendation.lower()


# ---------------------------------------------------------------------------
# Switch thermal scan now carries min/avg temps (S2 thermal history source)
# ---------------------------------------------------------------------------
def test_thermal_scan_result_includes_min_avg() -> None:
    from ai.switch.thermal import SwitchThermalAnalyzer
    from ai.thermal.base import ThermalFrame

    analyzer = SwitchThermalAnalyzer(
        baseline_frames=2,
        scan_frames=3,
        elevated_delta_c=10.0,
        abnormal_delta_c=20.0,
        critical_delta_c=35.0,
        rapid_rise_c_per_min=6.0,
    )
    temps = np.full((64, 64), 25.0, dtype=np.float32)
    temps[30:34, 30:34] = 50.0  # hotspot inside the ROI
    roi = (24, 24, 40, 40)
    for i in range(2):
        analyzer.add(ThermalFrame(temperatures=temps, timestamp=float(i)), None, roi, (64, 64))
    result = None
    for i in range(3):
        result = analyzer.add(ThermalFrame(temperatures=temps, timestamp=float(2 + i)), None, roi, (64, 64))
    assert result is not None and result.thermal_available
    assert result.min_temp is not None and result.min_temp <= result.max_temp
    assert result.avg_temp is not None and result.min_temp <= result.avg_temp <= result.max_temp
    payload = result.to_dict()
    assert "min_temp" in payload and "avg_temp" in payload
