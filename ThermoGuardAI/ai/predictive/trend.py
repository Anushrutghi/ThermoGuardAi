"""Temperature trend analysis for predictive maintenance.

Supports irregular time-series sampling, minimum history evaluation,
confidence intervals, goodness-of-fit (R²), and trend significance.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

import numpy as np
from scipy import stats


@dataclass
class TrendResult:
    """Robust statistical summary of temperature trend over time."""

    slope: float
    intercept: float
    r_squared: float
    stderr: float
    ci_95_lower: float
    ci_95_upper: float
    is_significant: bool
    n_observations: int
    duration_hours: float
    unit: str  # "c_per_day" or "c_per_step"
    status: str  # "VALID", "INSUFFICIENT_OBSERVATIONS", "INSUFFICIENT_DURATION", "FLAT_OR_COOLING"
    message: str


def _parse_timestamp(ts: datetime | str | float | int) -> float:
    """Convert a timestamp to epoch seconds float."""
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        # Handle ISO format
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    if isinstance(ts, datetime):
        return ts.timestamp()
    raise ValueError(f"Unsupported timestamp format: {ts!r}")


def analyze_trend(
    temperatures: Sequence[float],
    timestamps: Sequence[datetime | str | float | int] | None = None,
    min_observations: int = 5,
    min_duration_hours: float = 48.0,
) -> TrendResult:
    """Analyze thermal trend with statistical rigour, handling irregular sampling.

    Args:
        temperatures: Measured temperatures (°C) or temperature rises (ΔT).
        timestamps: Optional timestamps corresponding to temperatures.
        min_observations: Minimum required observations (default: 5).
        min_duration_hours: Minimum required observation span in hours (default: 48h).

    Returns:
        TrendResult containing slope, 95% CI, R², significance and data sufficiency status.
    """
    n = len(temperatures)
    if n < min_observations:
        return TrendResult(
            slope=0.0,
            intercept=float(temperatures[-1]) if n > 0 else 0.0,
            r_squared=0.0,
            stderr=0.0,
            ci_95_lower=0.0,
            ci_95_upper=0.0,
            is_significant=False,
            n_observations=n,
            duration_hours=0.0,
            unit="c_per_day" if timestamps else "c_per_step",
            status="INSUFFICIENT_OBSERVATIONS",
            message=f"Insufficient observations: {n} provided, minimum {min_observations} required for statistical trend analysis.",
        )

    # Calculate x values (in days if timestamps provided, else in steps)
    if timestamps is not None:
        if len(timestamps) != n:
            raise ValueError(f"Lengths mismatch: {len(temperatures)} temperatures vs {len(timestamps)} timestamps")
        epoch_secs = np.array([_parse_timestamp(t) for t in timestamps], dtype=float)
        # Sort if not monotonic
        sort_idx = np.argsort(epoch_secs)
        epoch_secs = epoch_secs[sort_idx]
        y = np.array([temperatures[i] for i in sort_idx], dtype=float)

        t0 = epoch_secs[0]
        duration_hours = (epoch_secs[-1] - t0) / 3600.0
        if duration_hours < min_duration_hours:
            return TrendResult(
                slope=0.0,
                intercept=float(y[-1]),
                r_squared=0.0,
                stderr=0.0,
                ci_95_lower=0.0,
                ci_95_upper=0.0,
                is_significant=False,
                n_observations=n,
                duration_hours=duration_hours,
                unit="c_per_day",
                status="INSUFFICIENT_DURATION",
                message=f"Observation window ({duration_hours:.1f}h) is too brief. Minimum {min_duration_hours:.0f}h span required to confirm structural thermal degradation.",
            )

        # x in elapsed days from first measurement
        x = (epoch_secs - t0) / 86400.0
        unit = "c_per_day"
    else:
        x = np.arange(n, dtype=float)
        y = np.array(temperatures, dtype=float)
        duration_hours = float(n)  # Nominal
        unit = "c_per_step"

    # Ordinary Least Squares Regression with stats
    x_mean = np.mean(x)
    y_mean = np.mean(y)
    ss_xx = np.sum((x - x_mean) ** 2)
    ss_yy = np.sum((y - y_mean) ** 2)
    ss_xy = np.sum((x - x_mean) * (y - y_mean))

    if ss_xx <= 1e-12:
        return TrendResult(
            slope=0.0,
            intercept=float(y_mean),
            r_squared=0.0,
            stderr=0.0,
            ci_95_lower=0.0,
            ci_95_upper=0.0,
            is_significant=False,
            n_observations=n,
            duration_hours=duration_hours,
            unit=unit,
            status="FLAT_OR_COOLING",
            message="No time variance in observations.",
        )

    slope = float(ss_xy / ss_xx)
    intercept = float(y_mean - slope * x_mean)
    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)

    r_squared = float(max(0.0, min(1.0, 1.0 - (ss_res / ss_yy)))) if ss_yy > 1e-12 else 0.0

    df = n - 2
    if df > 0:
        residual_variance = ss_res / df
        stderr = float(np.sqrt(max(0.0, residual_variance / ss_xx)))
        t_crit = float(stats.t.ppf(0.975, df=df))
        margin = t_crit * stderr
        ci_lower = slope - margin
        ci_upper = slope + margin
    else:
        stderr = 0.0
        ci_lower = slope
        ci_upper = slope

    # Statistical significance requires:
    # 1. 95% CI is strictly positive (slope > 0 with 95% confidence)
    # 2. R² >= 0.3 (clear trend explains variance above noise)
    is_significant = (ci_lower > 0.0) and (r_squared >= 0.3)

    if slope <= 0:
        status = "FLAT_OR_COOLING"
        msg = f"Component is stable or cooling ({slope:+.3f} {unit}, R²={r_squared:.2f}). No thermal runaway detected."
    elif not is_significant:
        status = "INCONCLUSIVE_NOISE"
        msg = f"Slight slope ({slope:+.3f} {unit}), but variance is high (R²={r_squared:.2f}, 95% CI: [{ci_lower:+.3f}, {ci_upper:+.3f}]). Consistent with ambient/load fluctuations rather than confirmed fault."
    else:
        status = "VALID"
        msg = f"Statistically significant heating trend: {slope:+.3f} {unit} (95% CI: [{ci_lower:+.3f}, {ci_upper:+.3f}], R²={r_squared:.2f})."

    return TrendResult(
        slope=round(slope, 4),
        intercept=round(intercept, 4),
        r_squared=round(r_squared, 4),
        stderr=round(stderr, 4),
        ci_95_lower=round(ci_lower, 4),
        ci_95_upper=round(ci_upper, 4),
        is_significant=is_significant,
        n_observations=n,
        duration_hours=round(duration_hours, 1),
        unit=unit,
        status=status,
        message=msg,
    )


def linear_trend(x: Sequence[float], y: Sequence[float]) -> tuple[float, float] | None:
    """Return (slope, intercept) of best-fit line, or None if insufficient data.

    Maintained for full backward compatibility with existing callers.
    """
    if len(x) < 2 or len(y) < 2:
        return None
    slope, intercept = np.polyfit(np.asarray(x, dtype=float), np.asarray(y, dtype=float), 1)
    return float(slope), float(intercept)


def smooth(values: list[float], alpha: float = 0.4) -> list[float]:
    """Exponential moving average (forecast smoothing)."""
    out: list[float] = []
    prev: float | None = None
    for v in values:
        prev = v if prev is None else alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def forecast(values: list[float], steps: int) -> list[float]:
    """Naive trend-based forecast of future values."""
    if not values:
        return []
    x = list(range(len(values)))
    fit = linear_trend(x, values)
    if fit is None:
        return [values[-1]] * steps
    slope, intercept = fit
    return [slope * (len(values) + i) + intercept for i in range(steps)]
