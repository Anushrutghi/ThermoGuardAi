"""Canonical dataset manifest schema for electrical component detection and visible fault recognition.

Enforces:
1. Strict separation between component detection classes and visible fault evidence classes.
2. Support for multi-label visible fault annotations on a single component.
3. Electrical safety verification protocol: connections cannot be labeled 'loose'
   from visual images without an audited verification procedure (torque test, micro-ohmmeter, etc.).
4. Explicit distinction between cosmetic discoloration/corrosion and functional failure.
5. Grouped split integrity: prevents adjacent video frames or identical panels from leaking across splits.
6. Full provenance tracking: site, panel, session, camera, timestamp, rights, reviewer, and version.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ============================================================================
# Enums
# ============================================================================

class ComponentClass(StrEnum):
    """Supported electrical component classes for object detection."""
    CIRCUIT_BREAKER = "circuit_breaker"
    MCB = "mcb"
    MCCB = "mccb"
    RCCB = "rccb"
    FUSE = "fuse"
    RELAY = "relay"
    CONTACTOR = "contactor"
    BUSBAR = "busbar"
    TERMINAL = "terminal"
    CABLE = "cable"
    TRANSFORMER = "transformer"
    MOTOR_STARTER = "motor_starter"
    DISCONNECT_SWITCH = "disconnect_switch"
    POWER_SUPPLY = "power_supply"
    INDICATOR_LIGHT = "indicator_light"
    PANEL_DOOR = "panel_door"
    WARNING_LABEL = "warning_label"


class VisibleFaultClass(StrEnum):
    """Visible optical anomalies observable in RGB inspection images.

    These are physical visual findings, NOT diagnostic fault hypotheses.
    """
    THERMAL_DISCOLORATION = "thermal_discoloration"  # Heat tint on metal, yellowing/browning on plastic
    SCORCHING_CHARRING = "scorching_charring"        # Carbon soot, localized burn marks, melted casing
    ARC_DAMAGE_PITTING = "arc_damage_pitting"        # Copper beads, electrical pitting, erosion
    SURFACE_CORROSION_OXIDATION = "surface_corrosion_oxidation"  # Rust, copper patina, aluminum bloom
    EXPOSED_UNINSULATED_CONDUCTOR = "exposed_uninsulated_conductor"  # Over-stripped or nicked insulation
    PHYSICAL_CASING_DAMAGE = "physical_casing_damage"  # Cracked housing, broken handle/toggle, chipped barrier
    MISSING_DEADFRONT_COVER = "missing_deadfront_cover"  # Missing blanking plate, exposed live busbar slot
    MECHANICAL_MISALIGNMENT = "mechanical_misalignment"  # Canted breaker on DIN rail, unlatched clip
    FOREIGN_OBJECT_DEBRIS = "foreign_object_debris"  # Dust layer, cobwebs, loose hardware, metal shavings
    MOISTURE_LIQUID_INGRESS = "moisture_liquid_ingress"  # Water droplets, condensation, dried mineral stains
    VERIFIED_LOOSE_CONNECTION = "verified_loose_connection"  # Gated: requires audited physical verification!


class HardNegativeType(StrEnum):
    """Visual patterns that mimic electrical faults but represent nominal/safe conditions."""
    MANUFACTURING_MARKING = "manufacturing_marking"  # Factory QC ink stamp, date code, molded logo
    SHADOW_OR_AMBIENT_DARKENING = "shadow_or_ambient_darkening"  # Wire shadow resembling scorching
    PROTECTIVE_COATING = "protective_coating"  # Conformal lacquer, anti-corrosion grease resembling liquid
    REFLECTION_GLARE = "reflection_glare"  # Bright specular highlight on plastic toggle resembling arc mark
    NON_ELECTRICAL_HARDWARE = "non_electrical_hardware"  # Zip tie, mounting bracket, DIN rail end stop


class VerificationSource(StrEnum):
    """Source of verification for fault ground truth."""
    VISUAL_EXPERT_REVIEW = "visual_expert_review"  # Level II thermographer or master electrician visual review
    CALIBRATED_TORQUE_TEST = "calibrated_torque_test"  # Torque wrench test to manufacturer specification
    MICRO_OHMMETER_TEST = "micro_ohmmeter_test"  # 4-wire DLRO low-resistance contact test
    THERMAL_RISE_UNDER_LOAD = "thermal_rise_under_load"  # Radiometric delta-T corroborated under measured load
    PHYSICAL_DISASSEMBLY = "physical_disassembly"  # Destructive/strip-down physical inspection
    UNVERIFIED = "unverified"


class SplitAssignment(StrEnum):
    """Dataset partition."""
    TRAIN = "train"
    VAL = "val"
    TEST = "test"


class ImageQualityFlag(StrEnum):
    """Optical image quality assessment."""
    OK = "ok"
    DARK = "dark"
    BLURRY = "blurry"
    GLARE = "glare"
    OCCLUDED = "occluded"


class ReviewStatus(StrEnum):
    """Review and curation lifecycle status."""
    PENDING_REVIEW = "PENDING_REVIEW"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"


class AnnotationStatus(StrEnum):
    """Annotation completeness."""
    UNANNOTATED = "UNANNOTATED"
    ANNOTATED = "ANNOTATED"
    VERIFIED = "VERIFIED"


# ============================================================================
# Models
# ============================================================================

class AnnotationBoundingBox(BaseModel):
    """Normalized bounding box coordinates [x_min, y_min, x_max, y_max] in range [0.0, 1.0]."""
    model_config = ConfigDict(extra="forbid")

    x_min: float = Field(..., ge=0.0, le=1.0, description="Normalized left coordinate (0.0 to 1.0).")
    y_min: float = Field(..., ge=0.0, le=1.0, description="Normalized top coordinate (0.0 to 1.0).")
    x_max: float = Field(..., ge=0.0, le=1.0, description="Normalized right coordinate (0.0 to 1.0).")
    y_max: float = Field(..., ge=0.0, le=1.0, description="Normalized bottom coordinate (0.0 to 1.0).")

    @model_validator(mode="after")
    def validate_box_extents(self) -> AnnotationBoundingBox:
        if self.x_min >= self.x_max:
            raise ValueError(f"x_min ({self.x_min}) must be strictly less than x_max ({self.x_max}).")
        if self.y_min >= self.y_max:
            raise ValueError(f"y_min ({self.y_min}) must be strictly less than y_max ({self.y_max}).")
        return self

    def to_yolo(self) -> tuple[float, float, float, float]:
        """Convert to YOLO format: (x_center, y_center, width, height) in 0..1."""
        width = self.x_max - self.x_min
        height = self.y_max - self.y_min
        x_center = self.x_min + (width / 2.0)
        y_center = self.y_min + (height / 2.0)
        return round(x_center, 6), round(y_center, 6), round(width, 6), round(height, 6)


class VisibleFaultAnnotation(BaseModel):
    """Verifiable visible optical anomaly associated with a component."""
    model_config = ConfigDict(extra="forbid")

    fault_class: VisibleFaultClass = Field(..., description="Visible anomaly class.")
    severity: str = Field(default="WARNING", description="Estimated risk: MONITORING, WARNING, CRITICAL.")
    bbox: AnnotationBoundingBox | None = Field(
        default=None,
        description="Optional localized bounding box of anomaly within the component (0.0 to 1.0).",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Reviewer confidence in this finding.")
    verification_source: VerificationSource = Field(
        default=VerificationSource.VISUAL_EXPERT_REVIEW,
        description="Source of verification for this label.",
    )
    verification_notes: str | None = Field(
        default=None,
        description="Detailed verification report or measurement notes (mandatory for verified_loose_connection).",
    )

    @model_validator(mode="after")
    def validate_loose_connection_rule(self) -> VisibleFaultAnnotation:
        """Enforces that an electrical connection cannot be labeled 'loose' from visual appearance alone."""
        if self.fault_class == VisibleFaultClass.VERIFIED_LOOSE_CONNECTION:
            permitted_sources = {
                VerificationSource.CALIBRATED_TORQUE_TEST,
                VerificationSource.MICRO_OHMMETER_TEST,
                VerificationSource.THERMAL_RISE_UNDER_LOAD,
                VerificationSource.PHYSICAL_DISASSEMBLY,
            }
            if self.verification_source not in permitted_sources:
                raise ValueError(
                    f"Invalid label '{self.fault_class}': An electrical connection CANNOT be labeled loose "
                    f"solely from an ordinary RGB visual image or '{self.verification_source.value}'. "
                    f"Must be verified by one of: {[s.value for s in permitted_sources]}."
                )
            if not self.verification_notes or not self.verification_notes.strip():
                raise ValueError(
                    f"Label '{self.fault_class}' requires non-empty verification_notes "
                    f"(e.g. measured torque value, resistance delta, or thermal load rise)."
                )
        return self


class ComponentAnnotation(BaseModel):
    """A detected electrical component instance with bounding box and visible faults."""
    model_config = ConfigDict(extra="forbid")

    component_class: ComponentClass = Field(..., description="Canonical component category.")
    bbox: AnnotationBoundingBox = Field(..., description="Component bounding box (0.0 to 1.0).")
    component_id: str | None = Field(default=None, description="Stable panel circuit code, e.g. 'B1', 'R2'.")
    is_healthy: bool = Field(default=True, description="True if component is free of visible defects.")
    visible_faults: list[VisibleFaultAnnotation] = Field(
        default_factory=list,
        description="List of verifiable visual anomalies on this component (multi-label).",
    )
    occluded: bool = Field(default=False, description="True if component is partially occluded by wiring/barrier.")
    truncated: bool = Field(default=False, description="True if component extends outside the frame border.")

    @model_validator(mode="after")
    def sync_health_state(self) -> ComponentAnnotation:
        if self.visible_faults and self.is_healthy:
            # If faults are annotated, the component is by definition not healthy
            self.is_healthy = False
        return self


class HardNegativeAnnotation(BaseModel):
    """An annotated benign region that mimics a fault to train the model to reject false alarms."""
    model_config = ConfigDict(extra="forbid")

    negative_type: HardNegativeType = Field(..., description="Type of benign visual artifact.")
    bbox: AnnotationBoundingBox = Field(..., description="Bounding box of the hard negative region.")
    mimicked_fault: VisibleFaultClass | None = Field(
        default=None,
        description="Visible fault that this artifact commonly mimics (e.g. scorching_charring).",
    )
    notes: str | None = Field(default=None, description="Explanation why this is a benign negative.")


class ImageMetadata(BaseModel):
    """Capture context, provenance, and data rights for a dataset sample."""
    model_config = ConfigDict(extra="forbid")

    site_id: str = Field(..., description="Facility or site identifier (e.g. 'FAC-DENVER-01').")
    panel_id: str = Field(..., description="Physical electrical panel identifier (e.g. 'PNL-MCC-04').")
    session_id: str = Field(..., description="Inspection recording session identifier.")
    camera_model: str | None = Field(default=None, description="Camera sensor hardware model.")
    timestamp: datetime = Field(..., description="Capture timestamp (UTC).")
    data_rights: str = Field(
        default="internal_inspection",
        description="Licensing or data rights policy (e.g. 'internal_inspection', 'open_dataset', 'customer_cleared').",
    )
    is_synthetic: bool = Field(default=False, description="True if synthetically generated / demo fixture.")
    synthetic_notes: str | None = Field(default=None, description="Notes if synthetic fixture.")
    environmental_conditions: dict[str, Any] = Field(
        default_factory=dict,
        description="Environmental metadata (e.g. lux, temperature_ambient_c, humidity_pct).",
    )

    @field_validator("timestamp", mode="after")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)


class SampleRecord(BaseModel):
    """Full metadata and annotation record for a single image sample in the dataset."""
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(..., description="Unique content-addressable hash (SHA-256) or UUID.")
    file_path: str = Field(..., description="Relative or absolute path to the preserved original image.")
    thumbnail_path: str | None = Field(default=None, description="Relative path to derived thumbnail image.")
    sha256: str = Field(..., description="Cryptographic SHA-256 checksum of original file.")
    dhash: str | None = Field(default=None, description="64-bit perceptual difference hash for near-duplicate detection.")
    width: int = Field(..., ge=320, description="Image pixel width.")
    height: int = Field(..., ge=320, description="Image pixel height.")
    metadata: ImageMetadata = Field(..., description="Provenance, site, panel, session, and rights metadata.")

    split: SplitAssignment | None = Field(default=None, description="Assigned split (train, val, test).")
    quality: ImageQualityFlag = Field(default=ImageQualityFlag.OK, description="Image quality rating.")
    components: list[ComponentAnnotation] = Field(default_factory=list, description="Annotated components.")
    hard_negatives: list[HardNegativeAnnotation] = Field(
        default_factory=list,
        description="Annotated hard negative regions.",
    )

    review_status: ReviewStatus = Field(
        default=ReviewStatus.PENDING_REVIEW,
        description="Curation review status (PENDING_REVIEW, APPROVED, REJECTED, QUARANTINED).",
    )
    annotation_status: AnnotationStatus = Field(
        default=AnnotationStatus.UNANNOTATED,
        description="Annotation progress.",
    )
    reviewer_id: str | None = Field(default=None, description="Identifier of human reviewer.")
    reviewed_at: datetime | None = Field(default=None, description="Timestamp of human review.")
    review_notes: str | None = Field(default=None, description="Reviewer feedback or notes.")

    duplicate_of: str | None = Field(default=None, description="SHA-256 of exact duplicate if detected.")
    near_duplicates: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of near-duplicate matches with Hamming distance scores.",
    )


class DatasetManifest(BaseModel):
    """Root dataset manifest specification enforcing grouped splits and schema compliance."""
    model_config = ConfigDict(extra="forbid")

    manifest_version: str = Field(default="1.0.0", description="Semver version of this dataset specification.")
    name: str = Field(..., description="Dataset or export name.")
    description: str | None = Field(default=None, description="Dataset description and scope.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Creation timestamp.")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last updated timestamp.")
    samples: list[SampleRecord] = Field(default_factory=list, description="List of sample records.")

    @model_validator(mode="after")
    def validate_grouped_split_leakage(self) -> DatasetManifest:
        """Enforce strict hierarchical grouping: the same panel_id or session_id

        MUST NEVER exist across multiple splits (e.g. train and test).
        """
        panel_to_splits: dict[str, set[str]] = {}
        session_to_splits: dict[str, set[str]] = {}

        for sample in self.samples:
            if not sample.split:
                continue
            split_val = sample.split.value
            panel_id = sample.metadata.panel_id
            session_id = sample.metadata.session_id

            panel_to_splits.setdefault(panel_id, set()).add(split_val)
            session_to_splits.setdefault(session_id, set()).add(split_val)

        leaking_panels = {p: s for p, s in panel_to_splits.items() if len(s) > 1}
        if leaking_panels:
            raise ValueError(
                f"Data leakage detected! The following panel_ids are assigned to multiple splits: {leaking_panels}. "
                f"All frames for a given panel MUST belong strictly to a single split (train, val, or test)."
            )

        leaking_sessions = {sess: s for sess, s in session_to_splits.items() if len(s) > 1}
        if leaking_sessions:
            raise ValueError(
                f"Data leakage detected! The following session_ids are assigned to multiple splits: {leaking_sessions}."
            )

        return self
