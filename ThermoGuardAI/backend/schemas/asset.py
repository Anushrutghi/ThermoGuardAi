"""Asset lifecycle management schemas."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class AssetComponentOut(BaseModel):
    id: int
    panel_id: int
    code: str | None = None
    label: str
    component_type: str
    installed_at: date | None = None
    expected_life_years: float = 15.0
    replacement_count: int = 0
    last_replaced_at: date | None = None
    retired: bool = False
    age_years: float | None = None
    life_used_pct: float | None = None
    reliability_score: float = 0.0
    health: str = "healthy"
    rul_hours: float | None = None
    failure_probability: float = 0.0
    trend_c_per_inspection: float = 0.0
    recommendation: str = ""
    failure_count: int = 0
    open_maintenance: int = 0
    total_repair_cost: float = 0.0
    last_maintenance_at: datetime | None = None


class PanelAssetOut(BaseModel):
    id: int
    name: str
    code: str
    location: str | None = None
    component_count: int = 0
    active_components: int = 0
    at_risk_components: int = 0
    avg_reliability: float = 0.0
    open_maintenance: int = 0
    total_repair_cost: float = 0.0
    last_inspection_at: datetime | None = None
    components: list[AssetComponentOut] = []


class AssetOverviewTotals(BaseModel):
    panels: int = 0
    components: int = 0
    active_components: int = 0
    at_risk_components: int = 0
    open_maintenance: int = 0
    total_repair_cost: float = 0.0
    avg_reliability: float = 0.0


class AssetOverview(BaseModel):
    panels: list[PanelAssetOut]
    totals: AssetOverviewTotals


class AssetFailureOut(BaseModel):
    id: int
    fault_type: str
    severity: str
    confidence: float = 0.0
    temperature: float | None = None
    message: str = ""
    recommendation: str | None = None
    created_at: datetime
    inspection_code: str | None = None


class AssetMaintenanceOut(BaseModel):
    id: int
    code: str
    status: str
    priority: str
    fault_type: str | None = None
    cost: float | None = None
    assigned_to: str | None = None
    deadline: date | None = None
    completed_at: datetime | None = None
    created_at: datetime


class ComponentAssetDetail(AssetComponentOut):
    panel_name: str | None = None
    panel_code: str | None = None
    panel_location: str | None = None
    limit_c: float | None = None
    temperature_series: list[dict] = []
    temperature_stats: dict = {}
    failures: list[AssetFailureOut] = []
    maintenance: list[AssetMaintenanceOut] = []


class ComponentUpdate(BaseModel):
    installed_at: date | None = None
    expected_life_years: float | None = Field(default=None, gt=0, le=100)
    retired: bool | None = None
    last_replaced_at: date | None = None
