"""Incident schemas — lifecycle management, creation, updates, and evidence representation."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.contracts import (
    FaultHypothesis,
    IncidentStatus,
    SeverityLevel,
    TemperatureReadingContract,
    VisibleEvidenceItem,
)
from backend.schemas.inspection import IncidentOut


class IncidentCreate(BaseModel):
    """Payload to create a new incident report."""
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, description="Optional custom incident code; auto-generated if omitted.")
    inspection_id: int | None = None
    device_id: int | None = None
    panel_id: int | None = None
    component_id: int | None = None
    component_label: str | None = None
    fault_type: str
    severity: str = "WARNING"
    temperature: float | None = None
    delta_t: float | None = None
    peer_comparison: str | None = None
    occurred_at: datetime | None = None
    suggested_action: str | None = None
    screenshot_path: str | None = None
    annotated_path: str | None = None
    evidence_thermal_path: str | None = None
    inspector: str | None = None
    notes: str | None = None


class IncidentUpdate(BaseModel):
    """Payload to update an existing incident's metadata or notes."""
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    suggested_action: str | None = None
    notes: str | None = None
    resolution_notes: str | None = None


class IncidentResolution(BaseModel):
    """Payload to mark an incident resolved or false positive."""
    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="RESOLVED", description="Target status: RESOLVED, FALSE_POSITIVE, or CLOSED.")
    resolution_notes: str = Field(..., description="Mandatory explanation of the resolution or why it is false positive.")
    resolved_by: str | None = None
    resolved_at: datetime | None = None


class IncidentFilter(BaseModel):
    """Filter parameters for querying incidents."""
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    severity: str | None = None
    device_id: int | None = None
    panel_id: int | None = None
    component_id: int | None = None
    inspection_id: int | None = None
    limit: int = 50
    offset: int = 0


class IncidentDetail(IncidentOut):
    """Detailed incident report including structured evidence contracts."""
    temperature_contract: TemperatureReadingContract | None = None
    visible_evidence: list[VisibleEvidenceItem] = []
    fault_hypotheses: list[FaultHypothesis] = []


__all__ = [
    "FaultHypothesis",
    "IncidentCreate",
    "IncidentDetail",
    "IncidentFilter",
    "IncidentOut",
    "IncidentResolution",
    "IncidentStatus",
    "IncidentUpdate",
    "SeverityLevel",
    "TemperatureReadingContract",
    "VisibleEvidenceItem",
]
