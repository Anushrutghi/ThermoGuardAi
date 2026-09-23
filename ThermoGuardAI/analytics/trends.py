"""S2 trend analysis — per-device temperature and thermal-delta trends.

Deterministic, configurable and honest: a trend is only declared when enough
historical points exist; otherwise an explicit "insufficient data" message is
returned. History is never fabricated — the caller supplies only real,
validated measurements (simulated DEMO rows are excluded upstream).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

STABLE = "STABLE"
SLIGHTLY_INCREASING = "SLIGHTLY_INCREASING"
INCREASING = "INCREASING"
RAPIDLY_INCREASING = "RAPIDLY_INCREASING"
DECREASING = "DECREASING"
INCONSISTENT = "INCONSISTENT"

INSUFFICIENT_MESSAGE = "Insufficient historical data for trend analysis."


@dataclass
class TrendResult:
    """Result of a trend classification over historical measurements."""

    status: str | None = None
    slope_c_per_inspection: float | None = None  # temperature trend slope (°C/inspection)
    slope_c_per_delta: float | None = None  # ΔT trend slope (°C/inspection)
    points: int = 0
    insufficient: bool = True
    message: str = INSUFFICIENT_MESSAGE


def _slope(values: Sequence[float]) -> float:
    """Least-squares slope over equally-spaced samples (index axis)."""
    if len(values) < 2:
        return 0.0
    xs = np.arange(len(values), dtype=float)
    return float(np.polyfit(xs, np.asarray(values, dtype=float), 1)[0])


def classify_temperature_trend(
    temps: Sequence[float],
    *,
    min_points: int = 3,
    decreasing_c: float = -0.5,
    stable_c: float = 0.5,
    slightly_c: float = 1.5,
    increasing_c: float = 3.0,
) -> TrendResult:
    """Classify the temperature trend across inspections (°C per inspection).

    Examples with the default thresholds:
      31 → 34 → 39 → 47 → 55  → slope ≈ +6   → RAPIDLY_INCREASING
      31 → 32 → 31 → 32 → 31  → slope ≈ 0    → STABLE
      55 → 47 → 39 → 34 → 31  → slope ≈ −6   → DECREASING
    """
    result = TrendResult(points=len(temps))
    if len(temps) < min_points:
        return result
    slope = _slope(temps)
    result.slope_c_per_inspection = round(slope, 3)
    if slope < decreasing_c:
        result.status = DECREASING
    elif slope < stable_c:
        result.status = STABLE
    elif slope < slightly_c:
        result.status = SLIGHTLY_INCREASING
    elif slope < increasing_c:
        result.status = INCREASING
    else:
        result.status = RAPIDLY_INCREASING
    result.insufficient = False
    result.message = "Trend computed from historical thermal data."
    return result


def classify_delta_trend(
    deltas: Sequence[float],
    *,
    min_points: int = 3,
    threshold_c: float = 0.5,
) -> TrendResult:
    """Classify the ΔT (switch − reference) trend across inspections.

    ΔT direction is measured between consecutive inspections. A series that
    swings meaningfully in both directions is reported INCONSISTENT instead of
    forcing a single direction; small noise around a flat mean is STABLE.
    """
    result = TrendResult(points=len(deltas))
    if len(deltas) < min_points:
        return result
    values = [float(d) for d in deltas]
    slope = _slope(values)
    result.slope_c_per_delta = round(slope, 3)

    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    directions = [1 if d > 0 else (-1 if d < 0 else 0) for d in diffs]
    flips = sum(
        1
        for i in range(1, len(directions))
        if directions[i] != 0 and directions[i - 1] != 0 and directions[i] != directions[i - 1]
    )
    mean_abs_swing = float(np.mean([abs(d) for d in diffs])) if diffs else 0.0

    if flips >= 2 and mean_abs_swing >= threshold_c:
        result.status = INCONSISTENT
    elif slope > threshold_c:
        result.status = INCREASING
    elif slope < -threshold_c:
        result.status = DECREASING
    else:
        result.status = STABLE
    result.insufficient = False
    result.message = "Delta trend computed from historical thermal data."
    return result
