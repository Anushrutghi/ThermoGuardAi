"""Inspection snapshot schema for immutable report generation.

Captures a frozen, verifiable point-in-time state of an inspection run, its
provenance, observed physical measurements, diagnostic inferences, incidents,
limitations, and operating context.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def compute_canonical_sha256(data: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 over a dictionary by canonical JSON serialization.

    Omits any existing 'snapshot_sha256' key to allow self-verification.
    """
    filtered = {k: v for k, v in data.items() if k != "snapshot_sha256"}
    canonical_json = json.dumps(filtered, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


class SnapshotProvenance(BaseModel):
    model_config = ConfigDict(extra="ignore")

    camera_id: str = "default-camera"
    camera_name: str = "Visible Optical Sensor"
    camera_transport: str = "direct-mediastream"
    thermal_source: str | None = None
    thermal_simulated: bool = False
    hardware_type: str = "REAL SENSOR"  # "REAL SENSOR" or "DEMO / SIMULATED"
    calibration_quality: str = "CALIBRATED_RADIOMETRIC"
    # Options: CALIBRATED_RADIOMETRIC, QUALITATIVE_NON_RADIOMETRIC, SIMULATED, SENSOR_UNAVAILABLE
    firmware_version: str | None = None
    sensor_resolution: str | None = None
    emissivity: float | None = None
    ambient_temp_c: float | None = None
    capture_start_time: str | None = None
    capture_end_time: str | None = None
    analysis_timestamp: str | None = None


class SnapshotScope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: str = "continuous"
    duration_seconds: float = 0.0
    frames_processed: int = 0
    target_fps: float = 15.0


class SnapshotPanel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    code: str = "PANEL-UNKNOWN"
    name: str = "Unspecified Panel"
    location: str | None = "—"
    rated_voltage: float | None = None
    rated_current: float | None = None
    organization: str | None = None


class SnapshotComponent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    label: str
    code: str | None = None
    component_type: str
    x: float = 0.0
    y: float = 0.0
    w: float = 0.1
    h: float = 0.1
    installed_at: str | None = None
    age_years: float | None = None
    replacement_count: int = 0


class SnapshotOperatingContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    operating_state: str = "UNKNOWN"
    # e.g., ONLINE_NORMAL_LOAD, ONLINE_HIGH_LOAD, STANDBY, UNKNOWN
    electrical_load_pct: float | None = None
    ambient_temp_c: float | None = None
    missing_context_flags: list[str] = Field(default_factory=list)
    # e.g., ["MISSING_AMBIENT_TEMPERATURE", "MISSING_ELECTRICAL_LOAD"]


class SnapshotObservedIndicator(BaseModel):
    model_config = ConfigDict(extra="ignore")

    component_label: str
    component_type: str = "component"
    max_temp_c: float | None = None
    avg_temp_c: float | None = None
    delta_t_c: float | None = None
    hotspot_x: int | None = None
    hotspot_y: int | None = None
    visual_discoloration_score: float | None = None
    smoke_spark_hypothesis_score: float | None = None


class SnapshotInferredCause(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fault_type: str
    severity: str
    confidence: float = 0.0
    component_label: str | None = None
    message: str = ""
    recommendation: str = ""
    model_version: str = "yolo11n-baseline-v1"
    rule_version: str = "rules-v1.4"
    ai_reasoning: str | None = None


class SnapshotIncident(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    code: str
    fault_type: str
    severity: str
    stage: str = "CONFIRMED"  # SUSPECTED, CONFIRMED, ESCALATED, RESOLVED, ACKNOWLEDGED
    component_label: str | None = None
    occurred_at: str
    acknowledged_at: str | None = None
    acknowledged_by: str | None = None
    assigned_to: str | None = None
    original_severity: str | None = None
    override_reason: str | None = None
    overridden_by: str | None = None
    escalation_history: list[dict[str, Any]] = Field(default_factory=list)
    resolved_at: str | None = None
    resolved_by: str | None = None
    resolution_notes: str | None = None


class InspectionSnapshot(BaseModel):
    """Immutable, verifiable point-in-time inspection snapshot."""

    model_config = ConfigDict(extra="ignore")

    snapshot_version: str = "1.0"
    report_id: str
    inspection_id: int
    inspection_code: str
    snapshot_timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    software_version: str = "ThermoGuard-AI-2.4"
    model_version: str = "yolo11n-baseline-v1"
    rule_version: str = "rules-v1.4"
    inspector: str = "—"
    risk_score: float = 0.0
    notes: str = ""

    scope: SnapshotScope = Field(default_factory=SnapshotScope)
    panel: SnapshotPanel = Field(default_factory=SnapshotPanel)
    provenance: SnapshotProvenance = Field(default_factory=SnapshotProvenance)
    operating_context: SnapshotOperatingContext = Field(default_factory=SnapshotOperatingContext)

    components: list[SnapshotComponent] = Field(default_factory=list)
    observed_indicators: list[SnapshotObservedIndicator] = Field(default_factory=list)
    inferred_causes: list[SnapshotInferredCause] = Field(default_factory=list)
    incidents: list[SnapshotIncident] = Field(default_factory=list)

    unresolved_limitations: list[str] = Field(default_factory=list)
    recommended_followup: list[str] = Field(default_factory=list)

    disclaimers: dict[str, str] = Field(
        default_factory=lambda: {
            "safety_certification": (
                "NON-CERTIFICATION NOTICE: This automated inspection report was generated by ThermoGuard AI as an "
                "inspection-assistance tool. This document does NOT certify electrical equipment safety, operational "
                "fitness, or compliance with NFPA 70E, IEEE, or OSHA standards. A physical examination by a licensed and "
                "certified electrical professional is required prior to taking equipment into or out of service."
            ),
            "signature_nature": (
                "The signature line below is a manual physical sign-off placeholder for printed inspection records and "
                "does NOT constitute a cryptographic digital signature."
            ),
        }
    )

    snapshot_sha256: str = ""

    def finalize_checksum(self) -> str:
        """Calculate and store the canonical SHA-256 hash."""
        raw_dict = self.model_dump()
        c_hash = compute_canonical_sha256(raw_dict)
        self.snapshot_sha256 = c_hash
        return c_hash

    def verify_integrity(self) -> bool:
        """Verify that the snapshot data matches its stored snapshot_sha256."""
        if not self.snapshot_sha256:
            return False
        return compute_canonical_sha256(self.model_dump()) == self.snapshot_sha256
