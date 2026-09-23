"""Canonical data contracts for electrical inspection observations, measurements, components, and incidents.

This module defines unambiguous, strongly-typed contracts enforcing:
1. Strict separation between measured, simulated, and missing temperatures.
2. Distinct representation of visible evidence vs. inferred fault hypotheses.
3. Explicit physical units (_c, _ms, _s, _pct, _m) and UTC ISO 8601 timestamps.
4. Non-zero encoding for missing temperature: physical 0.0 °C is valid cold
   temperature, while missing sensor readings are strictly None with an
   explicit TemperatureStatus reason.
5. Rich provenance tracking: sensor identity, model version, session track
   identity, and data quality metrics.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ============================================================================
# Enums
# ============================================================================

class ObservationProvenance(StrEnum):
    """Origin of an observation or measurement."""
    MEASURED = "MEASURED"      # Hardware sensor (e.g. FLIR, MLX90640, calibrated IR)
    SYNTHETIC = "SYNTHETIC"    # Simulation engine / demo dataset
    DERIVED = "DERIVED"        # Computed algorithmically (e.g. differential ΔT, ambient baseline)
    MANUAL = "MANUAL"          # Manually entered or verified by certified inspector


class ThermalDataQuality(StrEnum):
    """Quality and reliability state of thermal sensor data."""
    VALID = "VALID"            # Sensor calibrated, stable, high SNR
    UNSTABLE = "UNSTABLE"      # Sensor warming up or thermal drift detected
    DEGRADED = "DEGRADED"      # Low SNR, extreme ambient, or high reflection
    INVALID = "INVALID"        # Corrupted frame, packet loss, or sensor error
    SIMULATED = "SIMULATED"    # Synthetic demo frame — not a real thermal reading
    UNKNOWN = "UNKNOWN"        # Unspecified or uncalibrated


class TemperatureStatus(StrEnum):
    """Explicit status explaining the presence or absence of temperature data.

    MISSING temperatures must NEVER be represented as 0.0 °C.
    Physical 0.0 °C is valid cold-environment temperature.
    """
    MEASURED = "MEASURED"                    # Legitimate numeric reading from physical sensor
    SIMULATED = "SIMULATED"                  # Synthetic reading from simulation engine
    MISSING_NO_SENSOR = "MISSING_NO_SENSOR"  # No thermal sensor attached / configured
    MISSING_INVALID_FRAME = "MISSING_INVALID_FRAME"  # Frame corrupted, out-of-sync, or uncalibrated
    MISSING_OCCLUDED = "MISSING_OCCLUDED"    # Target component thermally occluded or outside FOV
    MISSING_OUT_OF_RANGE = "MISSING_OUT_OF_RANGE"  # Temperature outside sensor calibrated range


class CoverageStatus(StrEnum):
    """Visual or thermal coverage completeness of the target component."""
    FULL = "FULL"                            # Entire component visible in frame and thermal FOV
    PARTIAL = "PARTIAL"                      # Component partially clipped by frame edge or barrier
    OCCLUDED = "OCCLUDED"                    # Component largely obscured by wiring, cover, or objects
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"  # Insufficient frame quality/resolution to assess


class SeverityLevel(StrEnum):
    """Standardized severity classification."""
    NORMAL = "NORMAL"          # Nominal operation, within design tolerances
    MONITORING = "MONITORING"  # Minor variance, trend monitoring recommended
    WARNING = "WARNING"        # Action required in regular maintenance cycle
    CRITICAL = "CRITICAL"      # Immediate hazard: disconnect or urgent intervention


class IncidentStatus(StrEnum):
    """Lifecycle state of an evidence-backed incident."""
    OPEN = "OPEN"                      # Initial detection, pending review
    INVESTIGATING = "INVESTIGATING"    # Under review by maintenance engineer
    ACTIONED = "ACTIONED"              # Work order created / technician dispatched
    RESOLVED = "RESOLVED"              # Remediation verified and completed
    FALSE_POSITIVE = "FALSE_POSITIVE"  # Verified by inspector as non-fault / noise
    CLOSED = "CLOSED"                  # Incident administratively closed


class ComponentConditionState(StrEnum):
    """Aggregate health state of a physical electrical component."""
    HEALTHY = "HEALTHY"
    SUSPECT = "SUSPECT"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


# ============================================================================
# Contracts
# ============================================================================

class TemperatureReadingContract(BaseModel):
    """Explicit temperature reading separating measured, simulated, and missing states.

    CRITICAL RULE:
    Missing temperature MUST be None with a MISSING_* status.
    Physical 0.0 °C is ONLY permitted with MEASURED or SIMULATED status.
    """
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    status: TemperatureStatus = Field(
        ...,
        description="Status code specifying reading validity or exact reason for absence.",
    )
    temperature_c: float | None = Field(
        default=None,
        description="Observed temperature in degrees Celsius (°C). Null if missing.",
    )
    ambient_c: float | None = Field(
        default=None,
        description="Reference ambient temperature in degrees Celsius (°C).",
    )
    delta_t_c: float | None = Field(
        default=None,
        description="Rise over ambient or peer component differential (temperature_c - ambient_c) in °C.",
    )
    emissivity: float | None = Field(
        default=None,
        ge=0.01,
        le=1.0,
        description="Surface emissivity factor (0.01 - 1.00) used for radiation conversion.",
    )
    provenance: ObservationProvenance = Field(
        default=ObservationProvenance.MEASURED,
        description="Provenance of the temperature measurement.",
    )
    sensor_id: str | None = Field(
        default=None,
        description="Identifier of sensor hardware (e.g. 'mlx90640-i2c-1', 'flir-one-usb').",
    )
    measurement_confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Estimated confidence in the thermal measurement (0.0 to 1.0).",
    )

    @model_validator(mode="after")
    def validate_temperature_semantics(self) -> TemperatureReadingContract:
        # Rule 1: Missing temperature must have None value
        is_missing = self.status in {
            TemperatureStatus.MISSING_NO_SENSOR,
            TemperatureStatus.MISSING_INVALID_FRAME,
            TemperatureStatus.MISSING_OCCLUDED,
            TemperatureStatus.MISSING_OUT_OF_RANGE,
        }
        if is_missing:
            if self.temperature_c is not None:
                raise ValueError(
                    f"Temperature status {self.status.value} indicates missing data; "
                    f"temperature_c must be None, got {self.temperature_c}."
                )
        else:
            # Rule 2: MEASURED and SIMULATED status require a numeric temperature
            if self.temperature_c is None:
                raise ValueError(
                    f"Temperature status {self.status.value} requires a numeric temperature_c, "
                    f"but got None. Use a MISSING_* status if no reading was obtained."
                )

        # Rule 3: Synthetic provenance coherence
        if self.provenance == ObservationProvenance.SYNTHETIC and self.status == TemperatureStatus.MEASURED:
            raise ValueError("Synthetic provenance cannot have status MEASURED; must be SIMULATED.")

        if self.status == TemperatureStatus.SIMULATED and self.provenance == ObservationProvenance.MEASURED:
            # Coerce provenance to SYNTHETIC if marked SIMULATED
            self.provenance = ObservationProvenance.SYNTHETIC

        return self


class VisibleEvidenceItem(BaseModel):
    """Verifiable visual finding from RGB or optical inspection."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    anomaly_type: str = Field(
        ...,
        description="Category of visible anomaly, e.g. 'discoloration', 'burn_mark', 'corrosion', 'loose_wire', 'physical_damage'.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence of visual feature detector (0.0 to 1.0).",
    )
    bbox: list[float] | None = Field(
        default=None,
        description="Normalized bounding box [x1, y1, x2, y2] within the frame (0.0 to 1.0).",
    )
    description: str | None = Field(
        default=None,
        description="Inspector or model notes describing the visual evidence.",
    )
    evidence_image_path: str | None = Field(
        default=None,
        description="Filesystem or Cloud Storage URI of the cropped evidence image.",
    )


class FaultHypothesis(BaseModel):
    """An inferred diagnostic hypothesis, separated from raw physical measurements."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    fault_type: str = Field(
        ...,
        description="Hypothesized fault classification (e.g. 'loose_connection', 'overload', 'phase_imbalance', 'internal_wear').",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Model or rule confidence in this hypothesis (0.0 to 1.0).",
    )
    severity: SeverityLevel = Field(
        default=SeverityLevel.WARNING,
        description="Risk severity assigned to this hypothesis.",
    )
    inferred_from: list[str] = Field(
        default_factory=list,
        description="List of evidence signals backing this hypothesis, e.g. ['delta_t_c', 'visible_burn_mark', 'peer_comparison'].",
    )
    evidence_summary: str = Field(
        ...,
        description="Human-readable explanation of why this fault is hypothesized.",
    )
    recommended_action: str | None = Field(
        default=None,
        description="Actionable remediation recommendation for maintenance technicians.",
    )


class MeasurementContract(BaseModel):
    """Aggregate physical measurement bundle for an observation."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    temperature: TemperatureReadingContract = Field(
        ...,
        description="Temperature measurement details.",
    )
    thermal_quality: ThermalDataQuality = Field(
        default=ThermalDataQuality.VALID,
        description="Quality and reliability state of the thermal data.",
    )
    coverage: CoverageStatus = Field(
        default=CoverageStatus.FULL,
        description="Coverage completeness of the target component.",
    )
    distance_m: float | None = Field(
        default=None,
        ge=0.0,
        description="Estimated or measured distance from sensor to target in meters.",
    )
    reflected_temp_c: float | None = Field(
        default=None,
        description="Apparent reflected background temperature in degrees Celsius (°C).",
    )


class ObservationContract(BaseModel):
    """Canonical contract for a single inspected component observation within a frame."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    # Identifiers
    panel_id: int | None = Field(default=None, description="Database ID of the physical panel.")
    panel_code: str | None = Field(default=None, description="Human-readable panel code, e.g. 'PNL-MAIN-01'.")
    component_id: int | None = Field(default=None, description="Persistent database ID of the component.")
    component_code: str | None = Field(default=None, description="Display code within panel, e.g. 'B1', 'R2'.")
    component_label: str = Field(..., description="Classification label, e.g. 'circuit_breaker', 'relay'.")
    session_track_id: int | None = Field(
        default=None,
        description="Temporal tracker identity maintaining continuity across consecutive frames.",
    )
    frame_index: int = Field(default=0, ge=0, description="Sequential index of the frame within the session.")
    frame_id: str | None = Field(default=None, description="Unique frame hash or identifier for provenance auditing.")

    # Timestamps (Explicit UTC ISO 8601)
    capture_timestamp: datetime = Field(..., description="Timestamp when sensor captured the raw frame (UTC).")
    analysis_timestamp: datetime = Field(..., description="Timestamp when model completed inference (UTC).")
    analysis_age_ms: float = Field(
        ...,
        ge=0.0,
        description="Processing latency in milliseconds (analysis_timestamp - capture_timestamp).",
    )

    # Provenance & Model
    sensor_id: str | None = Field(default=None, description="Sensor hardware or pipeline identifier.")
    model_version: str | None = Field(default=None, description="Detection and thermal model version tag.")
    provenance: ObservationProvenance = Field(
        default=ObservationProvenance.MEASURED,
        description="Observation provenance (MEASURED, SYNTHETIC, DERIVED, MANUAL).",
    )

    # Spatial Detection
    bbox: list[float] | str = Field(
        ...,
        description="Bounding box coordinates [x1, y1, x2, y2] normalized (0..1) or serialized JSON.",
    )
    detection_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Object detector classification confidence (0.0 to 1.0).",
    )

    # Physical Measurements & Diagnostics
    measurement: MeasurementContract = Field(..., description="Thermal and optical physical measurements.")
    visible_evidence: list[VisibleEvidenceItem] = Field(
        default_factory=list,
        description="Verifiable visual anomalies observed on the component.",
    )
    fault_hypotheses: list[FaultHypothesis] = Field(
        default_factory=list,
        description="Inferred diagnostic hypotheses (separated from raw measurements).",
    )
    coverage_status: CoverageStatus = Field(
        default=CoverageStatus.FULL,
        description="Completeness of component coverage in this observation.",
    )
    insufficient_evidence: bool = Field(
        default=False,
        description="Flag set when low resolution, occlusion, or bad lighting precludes reliable assessment.",
    )

    @field_validator("capture_timestamp", "analysis_timestamp", mode="after")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def validate_observation_coherence(self) -> ObservationContract:
        # Check latency coherence
        delta_ms = (self.analysis_timestamp - self.capture_timestamp).total_seconds() * 1000.0
        # Allow small floating point tolerance (e.g. within 10 ms or explicit field)
        if delta_ms < -1.0:
            raise ValueError(
                f"analysis_timestamp ({self.analysis_timestamp}) cannot precede "
                f"capture_timestamp ({self.capture_timestamp})."
            )
        return self


class ComponentStateContract(BaseModel):
    """Persistent component state tracked across multiple inspection sessions."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    component_id: int = Field(..., description="Persistent component identifier.")
    component_code: str | None = Field(default=None, description="Display code within panel, e.g. 'B1'.")
    panel_id: int = Field(..., description="Parent panel identifier.")
    label: str = Field(..., description="Component label, e.g. 'Main Breaker'.")
    component_type: str = Field(..., description="Component category, e.g. 'circuit_breaker'.")
    condition_state: ComponentConditionState = Field(
        default=ComponentConditionState.HEALTHY,
        description="Current evaluated condition state.",
    )
    last_observation_time: datetime | None = Field(
        default=None,
        description="Timestamp of the most recent inspection observation (UTC).",
    )
    current_temperature_c: float | None = Field(
        default=None,
        description="Most recent observed temperature in °C (None if not measured or missing).",
    )
    historical_max_temp_c: float | None = Field(
        default=None,
        description="Peak temperature observed across all historical inspections in °C.",
    )
    baseline_temp_c: float | None = Field(
        default=None,
        description="Established normal operating baseline temperature in °C.",
    )
    open_incident_count: int = Field(
        default=0,
        ge=0,
        description="Number of unresolved incidents currently associated with this component.",
    )
    active_hypotheses: list[FaultHypothesis] = Field(
        default_factory=list,
        description="Active fault hypotheses pending resolution.",
    )
    installed_at: str | None = Field(default=None, description="Installation date (ISO 8601 YYYY-MM-DD).")
    expected_life_years: float = Field(default=15.0, ge=0.0, description="Expected operational life in years.")
    replacement_count: int = Field(default=0, ge=0, description="Number of times this component has been replaced.")


class EvidenceBackedIncidentContract(BaseModel):
    """An electrical incident backed by verifiable physical evidence, provenance, and lifecycle tracking."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    incident_code: str = Field(..., description="Unique incident identifier, e.g. 'INC-20260923-1-01'.")
    status: IncidentStatus = Field(
        default=IncidentStatus.OPEN,
        description="Lifecycle status (OPEN, INVESTIGATING, ACTIONED, RESOLVED, FALSE_POSITIVE, CLOSED).",
    )
    inspection_id: int | None = Field(default=None, description="ID of inspection where incident was detected.")
    device_id: int | None = Field(default=None, description="ID of associated electrical device / installation.")
    panel_id: int | None = Field(default=None, description="ID of associated electrical panel.")
    component_id: int | None = Field(default=None, description="ID of associated persistent component.")
    component_label: str | None = Field(default=None, description="Component name or classification label.")
    component_code: str | None = Field(default=None, description="Component display code, e.g. 'B1'.")

    # Diagnosis & Severity
    fault_type: str = Field(..., description="Primary fault type (e.g. 'overloaded_circuit', 'loose_connection').")
    severity: SeverityLevel = Field(..., description="Incident severity level.")

    # Physical Evidence & Measurements
    temperature: TemperatureReadingContract | None = Field(
        default=None,
        description="Detailed temperature contract for this incident.",
    )
    peer_delta_c: float | None = Field(
        default=None,
        description="Temperature differential above peer components under identical load in °C.",
    )
    visible_evidence: list[VisibleEvidenceItem] = Field(
        default_factory=list,
        description="Verifiable optical findings supporting this incident.",
    )
    fault_hypotheses: list[FaultHypothesis] = Field(
        default_factory=list,
        description="Diagnostic hypotheses explaining this incident.",
    )

    # Context & Timestamps
    occurred_at: datetime = Field(..., description="Incident detection timestamp (UTC).")
    screenshot_path: str | None = Field(default=None, description="Path to captured RGB evidence image.")
    annotated_path: str | None = Field(default=None, description="Path to annotated detection evidence image.")
    evidence_thermal_path: str | None = Field(default=None, description="Path to thermal radiometric evidence image.")
    inspector: str | None = Field(default=None, description="Username or ID of inspector / operator.")
    suggested_action: str | None = Field(default=None, description="Recommended remediation action.")
    notes: str | None = Field(default=None, description="Inspector operational notes.")

    # Resolution Audit Trail
    resolution_notes: str | None = Field(default=None, description="Remediation actions taken and verification notes.")
    resolved_at: datetime | None = Field(default=None, description="Timestamp when incident was resolved (UTC).")
    resolved_by: str | None = Field(default=None, description="User who resolved or closed the incident.")

    @field_validator("occurred_at", "resolved_at", mode="after")
    @classmethod
    def ensure_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)
