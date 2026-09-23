"""Incident model — automatic or inspector-logged incident report with lifecycle tracking."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.component import Component
    from backend.models.device import Device
    from backend.models.inspection import Inspection
    from backend.models.maintenance import MaintenanceRecord
    from backend.models.panel import Panel


class Incident(Base, TimestampMixin):
    """An evidence-backed incident report for an electrical fault or thermal anomaly."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), index=True, nullable=False)  # e.g. INC-20260807-3-01
    inspection_id: Mapped[int | None] = mapped_column(
        ForeignKey("inspections.id", ondelete="SET NULL"), index=True, nullable=True
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), index=True, nullable=True
    )
    panel_id: Mapped[int | None] = mapped_column(
        ForeignKey("panels.id", ondelete="SET NULL"), index=True, nullable=True
    )
    component_id: Mapped[int | None] = mapped_column(
        ForeignKey("components.id", ondelete="SET NULL"), index=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="OPEN", index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), default="CONFIRMED", index=True, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    reasons_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    organization: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    fault_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    component_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta_t: Mapped[float | None] = mapped_column(Float, nullable=True)
    peer_comparison: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    suggested_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    annotated_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    evidence_thermal_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    inspector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    inspection: Mapped[Inspection | None] = relationship(back_populates="incidents")
    device: Mapped[Device | None] = relationship()
    panel: Mapped[Panel | None] = relationship()
    component: Mapped[Component | None] = relationship()
    maintenance: Mapped[list[MaintenanceRecord]] = relationship(back_populates="incident")
