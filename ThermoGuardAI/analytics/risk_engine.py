"""S2 evidence-based risk engine.

Risk is NOT derived from a single hardcoded temperature. It combines the
configurable switch-vs-wall classification from S1 (which itself uses the
configurable thermal delta thresholds) with trend evidence, rapid-rise
detection and repeated anomalies.

Every result carries the risk level, the evidence list and the reasoning
behind it — nothing is asserted without evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

NORMAL = "NORMAL"
ELEVATED = "ELEVATED"
ABNORMAL = "ABNORMAL"
CRITICAL = "CRITICAL"

INSUFFICIENT_MESSAGE = "Insufficient data to assess risk."

# Base scores follow the S1 severity scale for each classification.
_BASE_SCORE = {NORMAL: 5.0, ELEVATED: 30.0, ABNORMAL: 60.0, CRITICAL: 88.0}

# Fault severity → conservative risk classification when no thermal scan
# exists but a real fault was recorded (visual findings etc.).
_SEVERITY_TO_CLASS = {"warning": ELEVATED, "high": ABNORMAL, "critical": CRITICAL}


def level_for_score(score: float) -> str:
    if score >= 80:
        return CRITICAL
    if score >= 50:
        return ABNORMAL
    if score >= 25:
        return ELEVATED
    return NORMAL


@dataclass
class RiskInputs:
    """Evidence gathered from the real database (never fabricated)."""

    latest_classification: str | None = None  # NORMAL|ELEVATED|ABNORMAL|CRITICAL
    latest_delta: float | None = None  # ΔT switch − reference (°C)
    heat_path: str | None = None  # localized|extended|unknown
    rapid_increase: bool = False
    temperature_trend: str | None = None
    repeated_anomaly: bool = False
    latest_fault_severity: str | None = None  # warning|high|critical
    has_any_inspection: bool = False
    has_any_fault: bool = False


@dataclass
class RiskResult:
    level: str | None = None
    risk_score: float | None = None
    evidence: list[str] = field(default_factory=list)
    reasoning: str = ""
    insufficient: bool = True
    message: str = INSUFFICIENT_MESSAGE


def compute_risk(
    inputs: RiskInputs,
    *,
    rapid_bonus: float = 10.0,
    repeat_bonus: float = 12.0,
) -> RiskResult:
    """Compute risk level + evidence + reasoning from real evidence."""
    result = RiskResult()
    if not inputs.has_any_inspection:
        result.message = INSUFFICIENT_MESSAGE
        return result

    classification = inputs.latest_classification
    if classification is None and inputs.has_any_fault:
        # No thermal scan data but real faults exist → derive the most recent
        # anomaly severity into a conservative classification.
        classification = _SEVERITY_TO_CLASS.get(inputs.latest_fault_severity or "", None)
    if classification is None:
        result.message = INSUFFICIENT_MESSAGE
        return result

    score = _BASE_SCORE.get(classification, 5.0)
    evidence: list[str] = []
    cls = classification

    if cls != NORMAL:
        if inputs.heat_path == "extended":
            evidence.append("Extended surface thermal pattern detected near the installation")
        elif inputs.heat_path == "localized":
            evidence.append("Localized hotspot detected")
        if inputs.latest_delta is not None:
            evidence.append(f"{inputs.latest_delta:+.1f} °C relative to reference")

    if inputs.rapid_increase:
        evidence.append("Rapid thermal increase during the latest scan")
        score += rapid_bonus

    trend = inputs.temperature_trend
    if trend == "RAPIDLY_INCREASING":
        evidence.append("Worsening (rapidly increasing) historical temperature trend")
        score = max(score, 60.0)
    elif trend == "INCREASING":
        evidence.append("Increasing historical temperature trend")
        score = max(score, 45.0)

    if inputs.repeated_anomaly:
        evidence.append("Similar anomaly found in previous inspections")
        score += repeat_bonus

    score = float(min(100.0, score))
    level = level_for_score(score)
    if evidence:
        reasoning = f"Risk assessed as {level} because: " + "; ".join(evidence) + "."
    elif classification == NORMAL:
        reasoning = f"Risk assessed as {level}: no thermal anomaly indicators found in the latest inspection."
    else:
        reasoning = (
            f"Risk assessed as {level} based on the recorded anomaly severity "
            f"({inputs.latest_fault_severity or 'recorded'}) from the latest inspection."
        )

    result.level = level
    result.risk_score = round(score, 1)
    result.evidence = evidence
    result.reasoning = reasoning
    result.insufficient = False
    result.message = "Risk assessed from inspection evidence."
    return result
