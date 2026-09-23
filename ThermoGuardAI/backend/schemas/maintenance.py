"""Maintenance management schemas."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from backend.schemas.common import ORMModel


class MaintenanceOut(ORMModel):
    id: int
    code: str
    inspection_id: int | None = None
    incident_id: int | None = None
    component_id: int | None = None
    component_label: str | None = None
    fault_type: str | None = None
    priority: str
    assigned_to: str | None = None
    status: str
    deadline: date | None = None
    notes: str | None = None
    cost: float | None = None
    completed_at: datetime | None = None
    created_at: datetime


class MaintenanceCreate(BaseModel):
    component_label: str | None = None
    component_id: int | None = None
    fault_type: str | None = None
    priority: str = Field(default="medium", pattern="^(critical|high|medium|low)$")
    assigned_to: str | None = None
    deadline: date | None = None
    notes: str | None = None
    cost: float | None = Field(default=None, ge=0)
    inspection_id: int | None = None


class MaintenanceUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(pending|in_progress|completed)$")
    assigned_to: str | None = None
    priority: str | None = Field(default=None, pattern="^(critical|high|medium|low)$")
    deadline: date | None = None
    notes: str | None = None
    cost: float | None = Field(default=None, ge=0)


class MaintenanceList(BaseModel):
    items: list[MaintenanceOut]
    total: int
    by_status: dict[str, int]
