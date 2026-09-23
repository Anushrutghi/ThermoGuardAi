"""S2 explainable device-health engine.

Produces a *deterministic, configurable, explainable* composite score from
evidence in the real database: latest thermal condition, temperature trend,
recent anomaly severity, repeated anomalies and inspection history.

Honesty requirement: the score is deliberately NOT presented as a
scientifically validated engineering measurement. Every result carries a
disclaimer and the per-contributor breakdown that produced it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

GOOD = "GOOD"
ATTENTION = "ATTENTION"
WARNING = "WARNING"
CRITICAL = "CRITICAL"

INSUFFICIENT_MESSAGE = "Insufficient data to calculate device health."

DISCLAIMER = (
    "Indicative health score derived from historical inspection data. "
    "This is not a scientifically validated engineering measurement and does "
    "not replace assessment by a qualified electrician."
)

# Default, configurable weights (sums to 1.0).
DEFAULT_WEIGHTS = {
    "thermal": 0.30,
    "trend": 0.25,
    "anomaly": 0.20,
    "repeated": 0.15,
    "history": 0.10,
}

# Thermal condition (latest classification) → score + rating
_CLASS_SCORE = {"NORMAL": 90.0, "ELEVATED": 65.0, "ABNORMAL": 40.0, "CRITICAL": 15.0}
_CLASS_RATING = {"NORMAL": GOOD, "ELEVATED": ATTENTION, "ABNORMAL": WARNING, "CRITICAL": CRITICAL}

# Temperature trend → score + rating
_TREND_SCORE = {
    "STABLE": 90.0,
    "DECREASING": 85.0,
    "SLIGHTLY_INCREASING": 70.0,
    "INCREASING": 50.0,
    "RAPIDLY_INCREASING": 20.0,
}
_TREND_RATING = {
    "STABLE": GOOD,
    "DECREASING": GOOD,
    "SLIGHTLY_INCREASING": ATTENTION,
    "INCREASING": WARNING,
    "RAPIDLY_INCREASING": CRITICAL,
}

# Recent anomaly severity (fault severity) → score + rating
_ANOMALY_SCORE = {"warning": 65.0, "high": 40.0, "critical": 15.0}
_ANOMALY_RATING = {"warning": "MEDIUM", "high": "HIGH", "critical": CRITICAL}


def _rating(score: float) -> str:
    if score >= 80:
        return GOOD
    if score >= 60:
        return ATTENTION
    if score >= 40:
        return WARNING
    return CRITICAL


@dataclass
class HealthContributor:
    """One explainable contribution to the health score."""

    name: str
    rating: str
    score: float
    weight: float
    detail: str


@dataclass
class HealthInputs:
    """Evidence gathered from the real database (never fabricated)."""

    latest_classification: str | None = None  # NORMAL|ELEVATED|ABNORMAL|CRITICAL
    temperature_trend: str | None = None  # STABLE|SLIGHTLY_INCREASING|INCREASING|RAPIDLY_INCREASING|DECREASING
    latest_fault_severity: str | None = None  # warning|high|critical
    repeated_anomaly: bool = False
    completed_inspections: int = 0


@dataclass
class HealthResult:
    score: int | None = None
    rating: str | None = None
    contributors: list[HealthContributor] = field(default_factory=list)
    insufficient: bool = True
    message: str = INSUFFICIENT_MESSAGE
    disclaimer: str = DISCLAIMER


def compute_health(
    inputs: HealthInputs,
    *,
    weights: dict[str, float] | None = None,
    min_inspections: int = 2,
) -> HealthResult:
    """Compute the deterministic, explainable health score.

    Contributors whose evidence is missing are simply omitted and the weights
    of the remaining contributors are renormalized — the result never pretends
    to have read data that is not there.
    """
    result = HealthResult()
    if inputs.completed_inspections < min_inspections:
        result.message = INSUFFICIENT_MESSAGE
        return result

    w = dict(weights or DEFAULT_WEIGHTS)
    total_weight = float(sum(w.values()))
    if total_weight <= 0:
        result.message = INSUFFICIENT_MESSAGE
        return result

    contributors: list[HealthContributor] = []

    if inputs.latest_classification in _CLASS_SCORE:
        cls = inputs.latest_classification
        contributors.append(
            HealthContributor(
                name="Thermal condition",
                rating=_CLASS_RATING[cls],
                score=_CLASS_SCORE[cls],
                weight=w["thermal"],
                detail=f"Latest inspection classified {cls}",
            )
        )

    if inputs.temperature_trend in _TREND_SCORE:
        trend = inputs.temperature_trend
        contributors.append(
            HealthContributor(
                name="Thermal trend",
                rating=_TREND_RATING[trend],
                score=_TREND_SCORE[trend],
                weight=w["trend"],
                detail=f"Historical temperature trend: {trend}",
            )
        )

    if inputs.latest_fault_severity is not None and inputs.latest_fault_severity in _ANOMALY_SCORE:
        severity = inputs.latest_fault_severity
        contributors.append(
            HealthContributor(
                name="Recent anomaly",
                rating=_ANOMALY_RATING[severity],
                score=_ANOMALY_SCORE[severity],
                weight=w["anomaly"],
                detail=f"Latest anomaly severity: {severity}",
            )
        )
    else:
        contributors.append(
            HealthContributor(
                name="Recent anomaly",
                rating=GOOD,
                score=90.0,
                weight=w["anomaly"],
                detail="No recent anomaly recorded",
            )
        )

    if inputs.repeated_anomaly:
        contributors.append(
            HealthContributor(
                name="Repeated anomaly",
                rating=ATTENTION,
                score=40.0,
                weight=w["repeated"],
                detail="Similar thermal anomaly found in previous inspections",
            )
        )
    else:
        contributors.append(
            HealthContributor(
                name="Repeated anomaly",
                rating=GOOD,
                score=90.0,
                weight=w["repeated"],
                detail="No repeated anomalies across inspections",
            )
        )

    count = inputs.completed_inspections
    if count >= 5:
        history_score, history_rating, detail = 90.0, GOOD, f"{count} completed inspections"
    elif count >= 3:
        history_score, history_rating, detail = 80.0, GOOD, f"{count} completed inspections"
    else:
        history_score, history_rating, detail = 70.0, ATTENTION, f"{count} completed inspections"
    contributors.append(
        HealthContributor(name="Inspection history", rating=history_rating, score=history_score, weight=w["history"], detail=detail)
    )

    available_weight = sum(c.weight for c in contributors)
    if available_weight <= 0:
        result.message = INSUFFICIENT_MESSAGE
        return result
    score = sum(c.score * c.weight for c in contributors) / available_weight

    result.score = int(round(score))
    result.rating = _rating(float(score))
    result.contributors = contributors
    result.insufficient = False
    result.message = "Device health computed from historical inspection evidence."
    return result
