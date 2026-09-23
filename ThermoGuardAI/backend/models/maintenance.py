"""Maintenance model — maintenance work orders created from critical faults."""
from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.component import Component
    from backend.models.incident import Incident
    from backend.models.inspection import Inspection


class MaintenanceRecord(Base, TimestampMixin):
    """A maintenance work order (e.g. MT-00012) linked to a fault/incident."""

    __tablename__ = "maintenance_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), index=True, nullable=False)  # e.g. MT-00012
    inspection_id: Mapped[int | None] = mapped_column(
        ForeignKey("inspections.id", ondelete="SET NULL"), index=True, nullable=True
    )
    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), index=True, nullable=True
    )
    component_id: Mapped[int | None] = mapped_column(
        ForeignKey("components.id", ondelete="SET NULL"), index=True, nullable=True
    )
    component_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fault_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium")  # critical|high|medium|low
    assigned_to: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|in_progress|completed
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)  # cost of the repair (lifecycle tracking)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    inspection: Mapped[Inspection | None] = relationship(back_populates="maintenance")
    incident: Mapped[Incident | None] = relationship(back_populates="maintenance")
    component: Mapped[Component | None] = relationship(back_populates="maintenance")
