"""Analytics schemas."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class TrendPoint(BaseModel):
    label: str
    value: float
    extra: dict[str, Any] = {}


class SeriesOut(BaseModel):
    name: str
    points: list[TrendPoint]


class AnalyticsSummary(BaseModel):
    period: str
    total_inspections: int
    total_faults: int
    critical_faults: int
    avg_risk: float
    top_fault_types: list[dict[str, Any]]
    severity_breakdown: dict[str, int]


class ComponentHealthOut(BaseModel):
    label: str
    component_type: str
    latest_temp: float | None = None
    avg_temp: float | None = None
    max_temp: float | None = None
    trend: float = 0.0  # °C / inspection (positive = warming)
    rul_hours: float | None = None
    failure_probability: float = 0.0
    health: str = "healthy"


class PredictiveOut(BaseModel):
    component_id: int | None = None
    label: str
    trend_c_per_inspection: float
    rul_hours: float | None
    failure_probability: float
    recommendation: str
