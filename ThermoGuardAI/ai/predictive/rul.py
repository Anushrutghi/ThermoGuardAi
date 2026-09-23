"""Threshold-crossing projection and calibrated outcome assessment.

Replaces pseudo-RUL (Remaining Useful Life) with honest mathematical
threshold-crossing extrapolations, explicitly stated assumptions,
and uncalibrated experimental indicators.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from ai.predictive.trend import TrendResult, analyze_trend, linear_trend

FORECAST_ASSUMPTIONS: list[str] = [
    "Constant electrical operating load equal to the recent inspection baseline.",
    "Ambient temperature remains stable at the historical mean.",
    "Linear monotonic thermal degradation with zero maintenance intervention.",
    "Surface temperature is an accurate surrogate for internal conductor junction degradation.",
]

RUL_HONESTY_DISCLAIMER: str = (
    "DISCLAIMER: Threshold-crossing projections represent mathematical extrapolations "
    "of temperature rise under assumed steady-state conditions. They do NOT constitute "
    "certified equipment remaining useful life (RUL), MTBF, or guarantees against sudden catastrophic failure."
)


@dataclass
class ThresholdCrossingProjection:
    """Rigorous threshold-crossing projection with confidence intervals and assumptions."""

    projected_days: float | None
    projected_operating_hours: float | None
    lower_bound_days: float | None  # 95% CI worst-case (fastest rise)
    upper_bound_days: float | None  # 95% CI best-case (slowest rise)
    target_limit_c: float
    current_temp_c: float
    remaining_delta_c: float
    trend_result: TrendResult
    status: str  # "PROJECTED", "NO_SIGNIFICANT_WARMING", "ALREADY_EXCEEDED", "INSUFFICIENT_DATA"
    assumptions: list[str] = field(default_factory=lambda: list(FORECAST_ASSUMPTIONS))
    disclaimer: str = RUL_HONESTY_DISCLAIMER


@dataclass
class FailureProbabilityAssessment:
    """Failure probability estimate with explicit calibration and evidence coverage."""

    probability: float
    calibration_status: str  # "UNCALIBRATED_EXPERIMENTAL" or "CALIBRATED_EMPIRICAL"
    confidence_level: str  # "LOW", "MEDIUM", "HIGH"
    data_coverage: str  # "INSUFFICIENT_LABELED_OUTCOMES", "ADEQUATE"
    explanation: str


def project_threshold_crossing(
    temperatures: Sequence[float],
    limit_c: float,
    timestamps: Sequence[datetime | str | float | int] | None = None,
    operating_hours_per_day: float = 24.0,
    min_observations: int = 5,
    min_duration_hours: float = 48.0,
) -> ThresholdCrossingProjection:
    """Project when a component's temperature will cross a critical threshold.

    Calculates point projection and 95% confidence bounds without fabricating
    unsupported certainty or treating threshold crossing as equipment RUL.
    """
    trend = analyze_trend(
        temperatures,
        timestamps=timestamps,
        min_observations=min_observations,
        min_duration_hours=min_duration_hours,
    )

    if not temperatures:
        return ThresholdCrossingProjection(
            projected_days=None,
            projected_operating_hours=None,
            lower_bound_days=None,
            upper_bound_days=None,
            target_limit_c=limit_c,
            current_temp_c=0.0,
            remaining_delta_c=limit_c,
            trend_result=trend,
            status="INSUFFICIENT_DATA",
        )

    current_temp = float(temperatures[-1])
    remaining_delta = limit_c - current_temp

    if remaining_delta <= 0:
        return ThresholdCrossingProjection(
            projected_days=0.0,
            projected_operating_hours=0.0,
            lower_bound_days=0.0,
            upper_bound_days=0.0,
            target_limit_c=limit_c,
            current_temp_c=current_temp,
            remaining_delta_c=0.0,
            trend_result=trend,
            status="ALREADY_EXCEEDED",
        )

    # If trend is not valid or heating is not statistically significant
    if trend.status != "VALID" or trend.slope <= 0 or not trend.is_significant:
        return ThresholdCrossingProjection(
            projected_days=None,
            projected_operating_hours=None,
            lower_bound_days=None,
            upper_bound_days=None,
            target_limit_c=limit_c,
            current_temp_c=current_temp,
            remaining_delta_c=round(remaining_delta, 2),
            trend_result=trend,
            status="NO_SIGNIFICANT_WARMING" if trend.status == "VALID" else trend.status,
        )

    # Point projection
    projected_days = remaining_delta / trend.slope
    projected_hours = projected_days * operating_hours_per_day

    # 95% Confidence Bounds (lower_bound_days corresponds to upper slope)
    lower_bound_days = (remaining_delta / trend.ci_95_upper) if trend.ci_95_upper > 0 else None
    upper_bound_days = (remaining_delta / trend.ci_95_lower) if trend.ci_95_lower > 0 else None

    return ThresholdCrossingProjection(
        projected_days=round(projected_days, 1),
        projected_operating_hours=round(projected_hours, 1),
        lower_bound_days=round(lower_bound_days, 1) if lower_bound_days is not None else None,
        upper_bound_days=round(upper_bound_days, 1) if upper_bound_days is not None else None,
        target_limit_c=limit_c,
        current_temp_c=round(current_temp, 2),
        remaining_delta_c=round(remaining_delta, 2),
        trend_result=trend,
        status="PROJECTED",
    )


def assess_failure_probability(
    temperatures: Sequence[float],
    limit_c: float,
) -> FailureProbabilityAssessment:
    """Assess failure probability, explicitly flagging uncalibrated experimental heuristic."""
    if not temperatures:
        return FailureProbabilityAssessment(
            probability=0.0,
            calibration_status="UNCALIBRATED_EXPERIMENTAL",
            confidence_level="LOW",
            data_coverage="INSUFFICIENT_LABELED_OUTCOMES",
            explanation="No temperature history available to evaluate probability.",
        )

    prob = failure_probability(list(temperatures), limit_c)
    return FailureProbabilityAssessment(
        probability=prob,
        calibration_status="UNCALIBRATED_EXPERIMENTAL",
        confidence_level="LOW",
        data_coverage="INSUFFICIENT_LABELED_OUTCOMES",
        explanation=(
            "Probability is an uncalibrated logistic heuristic based on proximity to standard thermal threshold "
            "and recent warming rate. This site lacks verified run-to-failure historical outcome datasets for empirical calibration."
        ),
    )


# -----------------------------------------------------------------------------
# Backward-Compatible Legacy Functions
# -----------------------------------------------------------------------------

def estimate_rul_hours(
    temperatures: list[float],
    limit_c: float,
    operating_hours_per_inspection: float = 6.0,
) -> float | None:
    """Estimate hours until temperature crosses the limit, based on trend.

    Retained for backward compatibility with legacy callers.
    """
    if len(temperatures) < 3:
        return None
    x = list(range(len(temperatures)))
    fit = linear_trend(x, temperatures)
    if fit is None:
        return None
    slope, intercept = fit
    if slope <= 0:  # not heating up
        return None
    if temperatures[-1] >= limit_c:  # already at/over limit
        return 0.0
    steps_to_limit = (limit_c - temperatures[-1]) / slope
    return max(0.0, steps_to_limit * operating_hours_per_inspection)


def failure_probability(temperatures: list[float], limit_c: float) -> float:
    """Logistic estimate of failure probability (0..1) based on temperature proximity + trend."""
    if not temperatures:
        return 0.0
    latest = temperatures[-1]
    ratio = max(0.0, (latest - limit_c * 0.5) / (limit_c * 0.5))  # 0 at half-limit, 1 at limit
    probability = 1.0 / (1.0 + math.exp(-6.0 * (ratio - 0.55)))
    # amplify with warming trend
    if len(temperatures) >= 3:
        slope, _ = linear_trend(list(range(len(temperatures))), temperatures) or (0.0, 0.0)
        if slope > 0.3:
            probability = min(0.99, probability + 0.1)
    return round(min(0.99, max(0.0, probability)), 4)


def recommendation_for(probability: float, rul_hours: float | None) -> str:
    """Human-readable maintenance recommendation."""
    if probability >= 0.8 or (rul_hours is not None and rul_hours <= 24):
        return "Critical thermal rise detected. Inspect immediately and verify connections under load."
    if probability >= 0.5 or (rul_hours is not None and rul_hours <= 72):
        return "Elevated thermal rise. Schedule physical inspection during upcoming maintenance window."
    if probability >= 0.25:
        return "Mild temperature elevation. Monitor during subsequent inspections."
    return "Operating within expected thermal baseline. Continue standard periodic monitoring."
