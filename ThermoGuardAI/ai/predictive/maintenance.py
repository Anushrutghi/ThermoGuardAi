"""Predictive maintenance engine.

Combines temperature history, irregular-time trend analysis,
operating context conditioning, and threshold-crossing projection
with explicit assumptions, uncertainty bounds, and replacement isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ai.fault.rules import threshold_for
from ai.predictive.rul import (
    FORECAST_ASSUMPTIONS,
    assess_failure_probability,
    estimate_rul_hours,
    failure_probability,
    project_threshold_crossing,
    recommendation_for,
)
from ai.predictive.trend import TrendResult, analyze_trend


@dataclass
class ComponentPrediction:
    """Predictive assessment for an electrical component."""

    component_id: int | None
    label: str
    component_type: str
    temperatures: list[float]
    trend_c_per_inspection: float  # Slope per step or per day
    rul_hours: float | None  # Legacy threshold-crossing hours (backward compatibility)
    failure_probability: float
    recommendation: str
    health: str

    # Upgraded statistical rigor and honesty fields
    projected_crossing_days: float | None = None
    crossing_ci_95_days: tuple[float | None, float | None] | None = None
    r_squared: float = 0.0
    trend_stderr: float = 0.0
    trend_ci_95: tuple[float, float] = (0.0, 0.0)
    trend_significant: bool = False
    data_status: str = "INSUFFICIENT_DATA"
    probability_status: str = "UNCALIBRATED_EXPERIMENTAL"
    assumptions: list[str] = field(default_factory=lambda: list(FORECAST_ASSUMPTIONS))
    operating_context: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""

    @property
    def limit_c(self) -> float:
        _, high, critical = threshold_for(self.component_type)
        return critical


class PredictiveMaintenanceEngine:
    """Runs statistical thermal trend forecasting and threshold projection."""

    def __init__(
        self,
        min_observations: int = 5,
        min_duration_hours: float = 48.0,
        operating_hours_per_day: float = 24.0,
    ) -> None:
        self.min_observations = min_observations
        self.min_duration_hours = min_duration_hours
        self.operating_hours_per_day = operating_hours_per_day

    def predict(self, history: list[dict[str, Any]]) -> list[ComponentPrediction]:
        """Produce rigorous predictive forecasts from historical component observations.

        History dictionary schema per item:
            - component_id: int | None
            - label: str
            - component_type: str
            - temperatures: list[float]
            - timestamps: list[datetime | str | float] (optional)
            - ambients: list[float] (optional)
            - last_replaced_at: date | str | None (optional)
        """
        predictions: list[ComponentPrediction] = []

        for item in history:
            raw_temps = list(item.get("temperatures", []))
            timestamps = list(item.get("timestamps", [])) if item.get("timestamps") else None
            ambients = list(item.get("ambients", [])) if item.get("ambients") else None
            last_replaced_at = item.get("last_replaced_at")
            component_type = item.get("component_type", "circuit_breaker")
            label = item.get("label", "component")
            # 1. Determine effective threshold:
            # If explicitly specified in item, use it.
            # If ambients provided, analysis_series is delta T (use delta limit, e.g. 30°C).
            # If absolute temps provided without ambients, add nominal ambient (25°C) to delta limit.
            if "limit_c" in item:
                limit = float(item["limit_c"])
            elif ambients:
                limit = threshold_for(component_type)[2]
            else:
                limit = 25.0 + threshold_for(component_type)[2]

            # 2. Filter out observations prior to asset replacement
            temps = raw_temps
            active_timestamps = timestamps
            active_ambients = ambients
            if last_replaced_at and timestamps:
                cutoff_dt = (
                    datetime.combine(last_replaced_at, datetime.min.time())
                    if isinstance(last_replaced_at, date) and not isinstance(last_replaced_at, datetime)
                    else (datetime.fromisoformat(str(last_replaced_at).replace("Z", "+00:00")) if isinstance(last_replaced_at, str) else last_replaced_at)
                )
                filtered_indices = []
                for idx, ts in enumerate(timestamps):
                    ts_dt = ts if isinstance(ts, datetime) else (datetime.fromtimestamp(float(ts)) if isinstance(ts, (int, float)) else datetime.fromisoformat(str(ts).replace("Z", "+00:00")))
                    if ts_dt >= cutoff_dt:
                        filtered_indices.append(idx)
                if filtered_indices:
                    temps = [raw_temps[i] for i in filtered_indices]
                    active_timestamps = [timestamps[i] for i in filtered_indices]
                    if ambients and len(ambients) == len(raw_temps):
                        active_ambients = [ambients[i] for i in filtered_indices]
                else:
                    temps = []
                    active_timestamps = []
                    active_ambients = []

            # 3. Operating Context Conditioning
            # If ambient temperatures are available, compute delta T to strip seasonal/ambient weather variation
            analysis_series = temps
            context_note = "Raw surface temperature (ambient context unmonitored)."
            if active_ambients and len(active_ambients) == len(temps) and all(a is not None for a in active_ambients):
                analysis_series = [t - a for t, a in zip(temps, active_ambients)]
                context_note = "Rise-over-ambient ΔT (conditioned on local ambient temperature)."

            # 4. Statistical Trend Analysis
            trend: TrendResult = analyze_trend(
                analysis_series,
                timestamps=active_timestamps,
                min_observations=self.min_observations,
                min_duration_hours=self.min_duration_hours,
            )

            # 5. Threshold-Crossing Projection
            crossing = project_threshold_crossing(
                temperatures=analysis_series,
                limit_c=limit,
                timestamps=active_timestamps,
                operating_hours_per_day=self.operating_hours_per_day,
                min_observations=self.min_observations,
                min_duration_hours=self.min_duration_hours,
            )

            # 6. Failure Probability Assessment (honest experimental indicator)
            prob_assessment = assess_failure_probability(analysis_series, limit)
            prob = prob_assessment.probability

            # 6. Legacy compatibility fallbacks for minimal observation histories (< 5)
            # If len(temps) in [3, 4] and no timestamps, allow legacy linear_trend for backward test compatibility
            legacy_trend = trend.slope
            legacy_rul = estimate_rul_hours(temps, limit) if len(temps) < self.min_observations else crossing.projected_operating_hours
            if len(temps) >= 3 and trend.status == "INSUFFICIENT_OBSERVATIONS":
                from ai.predictive.trend import linear_trend
                fit = linear_trend(list(range(len(temps))), temps)
                if fit:
                    legacy_trend = round(fit[0], 3)

            rec = recommendation_for(prob, legacy_rul)

            # 7. Health Determination
            health = "healthy"
            if not temps:
                health = "insufficient_data"
            elif prob >= 0.8 or (legacy_rul is not None and legacy_rul <= 24):
                health = "critical"
            elif prob >= 0.5 or (legacy_rul is not None and legacy_rul <= 72):
                health = "high"
            elif prob >= 0.25 or (trend.slope > 0.3 and trend.is_significant):
                health = "warning"

            predictions.append(
                ComponentPrediction(
                    component_id=item.get("component_id"),
                    label=label,
                    component_type=component_type,
                    temperatures=temps,
                    trend_c_per_inspection=legacy_trend,
                    rul_hours=round(legacy_rul, 1) if legacy_rul is not None else None,
                    failure_probability=prob,
                    recommendation=rec,
                    health=health,
                    projected_crossing_days=crossing.projected_days,
                    crossing_ci_95_days=(crossing.lower_bound_days, crossing.upper_bound_days) if crossing.lower_bound_days is not None else None,
                    r_squared=trend.r_squared,
                    trend_stderr=trend.stderr,
                    trend_ci_95=(trend.ci_95_lower, trend.ci_95_upper),
                    trend_significant=trend.is_significant,
                    data_status=trend.status,
                    probability_status=prob_assessment.calibration_status,
                    assumptions=list(crossing.assumptions),
                    operating_context={
                        "context_note": context_note,
                        "conditioned_on_ambient": active_ambients is not None,
                        "observations_count": len(temps),
                        "duration_hours": trend.duration_hours,
                        "replaced_at": str(last_replaced_at) if last_replaced_at else None,
                    },
                    explanation=trend.message,
                )
            )

        return predictions

    def build_summary(self, predictions: list[ComponentPrediction]) -> str:
        """Construct an honest natural-language summary separating observation, forecast, and caveats."""
        if not predictions:
            return "No component temperature history available for trend analysis."

        valid = [p for p in predictions if p.data_status == "VALID" and p.trend_significant]
        insufficient = [p for p in predictions if p.data_status.startswith("INSUFFICIENT")]

        lines: list[str] = []

        if valid:
            worst = max(valid, key=lambda p: p.failure_probability)
            lines.append(
                f"Observed warming trend on {worst.label} ({worst.trend_c_per_inspection:+.2f} °C, R²={worst.r_squared:.2f})."
            )
            if worst.projected_crossing_days is not None:
                ci_note = ""
                if worst.crossing_ci_95_days and all(d is not None for d in worst.crossing_ci_95_days):
                    ci_note = f" (95% CI: {worst.crossing_ci_95_days[0]:.1f} – {worst.crossing_ci_95_days[1]:.1f} days)"
                lines.append(
                    f"Under steady load and ambient conditions, projected limit crossing is ~{worst.projected_crossing_days:.1f} days{ci_note}."
                )
            lines.append(worst.recommendation)
        elif predictions:
            highest = max(predictions, key=lambda p: p.failure_probability)
            if highest.failure_probability >= 0.5:
                lines.append(f"Highest current temperature: {highest.label} — {highest.recommendation}")
            else:
                lines.append("All components with history are operating within standard thermal bounds.")

        if insufficient:
            lines.append(f"({len(insufficient)} component(s) have insufficient history for statistically valid projection).")

        return " ".join(lines)
