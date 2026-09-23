"""S2 maintenance recommendations — generated from actual evidence only.

The system never claims a confirmed electrical fault; language stays
conservative ("thermal anomaly detected", "professional inspection
recommended") unless the evidence genuinely supports a stronger statement.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from analytics.risk_engine import ABNORMAL, CRITICAL, ELEVATED
from analytics.trends import INCREASING, RAPIDLY_INCREASING


@dataclass
class RecommendationResult:
    recommendation: str
    basis: list[str] = field(default_factory=list)
    insufficient: bool = False


def maintenance_recommendation(
    *,
    risk_level: str | None = None,
    temperature_trend: str | None = None,
    repeated_anomaly: bool = False,
) -> RecommendationResult:
    """Recommend a maintenance action from risk/trend/repeat evidence."""
    if risk_level is None:
        return RecommendationResult(
            "Insufficient inspection data to generate a maintenance recommendation.",
            insufficient=True,
        )

    basis: list[str] = []
    if risk_level == CRITICAL:
        basis.append("Critical thermal anomaly level")
        recommendation = (
            "Potential serious thermal anomaly detected. Avoid touching or opening "
            "the installation and have it evaluated by a qualified electrician."
        )
    elif risk_level == ABNORMAL or repeated_anomaly:
        if repeated_anomaly:
            basis.append("Repeated thermal anomaly detected on this device")
        if risk_level == ABNORMAL:
            basis.append("Abnormal thermal risk level")
        recommendation = "Professional electrical inspection recommended."
    elif temperature_trend in (INCREASING, RAPIDLY_INCREASING):
        basis.append(f"{temperature_trend} temperature trend")
        recommendation = "Thermal condition is worsening. Schedule professional inspection."
    elif risk_level == ELEVATED:
        basis.append("Elevated thermal risk level")
        recommendation = "Monitor the device; re-inspect if the thermal pattern persists or worsens."
    else:  # NORMAL
        basis.append("Normal thermal risk level")
        recommendation = "Continue routine inspection."

    return RecommendationResult(recommendation=recommendation, basis=basis, insufficient=False)
