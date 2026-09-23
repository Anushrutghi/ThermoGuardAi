"""Inspection model — one session of inspection (manual / live / scheduled)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.alarm import Alarm
    from backend.models.detection import Detection
    from backend.models.device import Device
    from backend.models.fault import Fault
    from backend.models.incident import Incident
    from backend.models.maintenance import MaintenanceRecord
    from backend.models.panel import Panel
    from backend.models.report import Report
    from backend.models.thermal_history import ThermalHistory
    from backend.models.user import User


class Inspection(Base, TimestampMixin):
    """A single inspection session."""

    __tablename__ = "inspections"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    panel_id: Mapped[int | None] = mapped_column(ForeignKey("panels.id", ondelete="SET NULL"), index=True, nullable=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), index=True, nullable=True)
    mode: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)  # quick|continuous|manual|scheduled|emergency|switch_first
    camera_source: Mapped[str] = mapped_column(String(64), default="webcam")
    status: Mapped[str] = mapped_column(String(32), default="running")  # running|completed|aborted
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frames_processed: Mapped[int] = mapped_column(Integer, default=0)
    avg_fps: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)  # 0..100
    component_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Professional inspection management fields
    inspection_code: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)  # TG-20260807-00015
    software_version: Mapped[str | None] = mapped_column(String(32), nullable=True)  # backend version at inspection time
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)  # detector engine / model used
    # Thermal honesty: which source produced the temperatures, and whether they
    # were simulated (DEMO). Reports/dashboards must label simulated data.
    thermal_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    thermal_simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # soft-archived from history
    # Screenshot evidence captured during the inspection (embedded in PDF reports)
    original_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    annotated_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    thermal_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User | None] = relationship(back_populates="inspections")
    panel: Mapped[Panel | None] = relationship(back_populates="inspections")
    device: Mapped[Device | None] = relationship(back_populates="inspections")
    detections: Mapped[list[Detection]] = relationship(
        back_populates="inspection", cascade="all, delete-orphan", order_by="Detection.id"
    )
    faults: Mapped[list[Fault]] = relationship(
        back_populates="inspection", cascade="all, delete-orphan", order_by="Fault.id"
    )
    alarms: Mapped[list[Alarm]] = relationship(back_populates="inspection")
    incidents: Mapped[list[Incident]] = relationship(
        back_populates="inspection", cascade="all, delete-orphan", order_by="Incident.id"
    )
    maintenance: Mapped[list[MaintenanceRecord]] = relationship(
        back_populates="inspection", cascade="all, delete-orphan"
    )
    thermal_history: Mapped[list[ThermalHistory]] = relationship(
        back_populates="inspection", cascade="all, delete-orphan", order_by="ThermalHistory.timestamp"
    )
    reports: Mapped[list[Report]] = relationship(
        back_populates="inspection", cascade="all, delete-orphan", order_by="Report.generated_at"
    )

    @property
    def duration_seconds(self) -> float:
        if not self.ended_at:
            return 0.0
        start, end = self.started_at, self.ended_at
        # SQLite returns naive datetimes while we store aware UTC values;
        # normalize before subtracting to avoid naive/aware mixing errors.
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        return max(0.0, (end - start).total_seconds())

    @property
    def duration_s(self) -> float:
        return self.duration_seconds

    @property
    def health_score(self) -> float:
        """Overall health 0..100 (inverse of the risk score)."""
        return round(max(0.0, min(100.0, 100.0 - self.risk_score)), 1)

    @property
    def inspector_name(self) -> str | None:
        return self.user.username if self.user else None

    @property
    def panel_name(self) -> str | None:
        return self.panel.name if self.panel else None

    @property
    def panel_code(self) -> str | None:
        return self.panel.code if self.panel else None

    @property
    def panel_location(self) -> str | None:
        return self.panel.location if self.panel else None

    @property
    def device_name(self) -> str | None:
        return self.device.name if self.device else None

    @property
    def device_location(self) -> str | None:
        return self.device.location if self.device else None
