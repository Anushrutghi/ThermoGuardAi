"""Inspection, detection and fault schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backend.schemas.common import ORMModel


class DetectionOut(ORMModel):
    id: int
    label: str
    confidence: float
    bbox: str
    temperature: float | None = None
    health: str
    frame_index: int
    created_at: datetime | None = None
    component_code: str | None = None  # stable display code, e.g. B1 (injected in route)
    # --- Additive data contract fields ---
    component_id: int | None = None
    session_track_id: int | None = None
    provenance: str | None = None
    thermal_quality: str | None = None
    coverage: str | None = None


class AlarmOut(ORMModel):
    id: int
    severity: str
    message: str
    created_at: datetime
    media_path: str | None = None


class FaultOut(ORMModel):
    id: int
    component_label: str | None = None
    fault_type: str
    severity: str
    confidence: float
    temperature: float | None = None
    message: str
    recommendation: str | None = None
    # --- Additive data contract fields ---
    component_id: int | None = None
    delta_t: float | None = None
    peer_delta: float | None = None
    hypotheses: list[str] = []


class IncidentOut(ORMModel):
    id: int
    code: str
    inspection_id: int | None = None
    fault_type: str
    severity: str
    component_label: str | None = None
    temperature: float | None = None
    occurred_at: datetime
    suggested_action: str | None = None
    screenshot_path: str | None = None
    annotated_path: str | None = None
    inspector: str | None = None
    notes: str | None = None
    # --- Additive data contract fields ---
    status: str = "OPEN"
    device_id: int | None = None
    panel_id: int | None = None
    component_id: int | None = None
    component_code: str | None = None
    delta_t: float | None = None
    peer_comparison: str | None = None
    evidence_thermal_path: str | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_notes: str | None = None



class SeverityCounts(BaseModel):
    healthy: int = 0
    warning: int = 0
    high: int = 0
    critical: int = 0


class TemperatureStats(BaseModel):
    max_temp: float | None = None
    min_temp: float | None = None
    avg_temp: float | None = None


class InspectionOut(ORMModel):
    id: int
    user_id: int | None = None
    panel_id: int | None = None
    device_id: int | None = None
    mode: str
    camera_source: str
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    frames_processed: int
    avg_fps: float
    risk_score: float
    component_count: int
    notes: str | None = None
    # --- professional history fields ---
    inspection_code: str | None = None
    software_version: str | None = None
    model_version: str | None = None
    # Thermal honesty: simulated sources must be labeled DEMO everywhere.
    thermal_source: str | None = None
    thermal_simulated: bool = False
    archived: bool = False
    health_score: float = 0.0
    duration_s: float = 0.0
    inspector: str | None = None
    panel_name: str | None = None
    panel_code: str | None = None
    panel_location: str | None = None
    device_name: str | None = None
    device_location: str | None = None
    # Screenshot evidence captured during the inspection (embedded in PDFs)
    original_image_path: str | None = None
    annotated_image_path: str | None = None
    thermal_image_path: str | None = None
    counts: SeverityCounts = SeverityCounts()
    temperature_stats: TemperatureStats = TemperatureStats()
    faults_count: int = 0


class InspectionDetail(InspectionOut):
    detections: list[DetectionOut] = []
    faults: list[FaultOut] = []
    incidents: list[IncidentOut] = []
    alarms: list[AlarmOut] = []
    temperature_series: list[dict] = []  # [{time, label, temp}]
    report_generated_at: datetime | None = None


class InspectionStart(BaseModel):
    mode: str = Field(default="manual", pattern="^(quick|continuous|manual|scheduled|emergency|switch_first)$")
    panel_id: int | None = None
    device_id: int | None = None
    camera_source: str = "webcam"
    notes: str | None = None


class InspectionStop(BaseModel):
    notes: str | None = None


class InspectionStats(BaseModel):
    total: int
    today: int
    open_alarms: int
    avg_risk: float
    by_severity: dict[str, int]
