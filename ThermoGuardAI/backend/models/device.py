"""Device model — the physical electrical device under inspection (S1 + S2).

A device is a specific electrical installation point (e.g. "Living Room
Switch", "Kitchen Socket"). Every inspection belongs to exactly one device;
all dashboards, graphs, reports and history are isolated per device.

S2 (device intelligence): the model carries install/brand metadata, and the
*derived* operational status is persisted so dashboard aggregation is fast.
`derived_status` is computed from real inspection/risk data (see
backend.services.device_analytics) and may only be overridden manually
(MAINTENANCE / INACTIVE) by an authorized administrator.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.inspection import Inspection
    from backend.models.thermal_history import ThermalHistory

# Canonical S2 operational statuses (see DeviceAnalyticsService.derive_status).
ACTIVE = "ACTIVE"
MONITORING = "MONITORING"
ATTENTION = "ATTENTION"
HIGH_RISK = "HIGH_RISK"
CRITICAL = "CRITICAL"
MAINTENANCE = "MAINTENANCE"
INACTIVE = "INACTIVE"

OPERATIONAL_STATUSES = (ACTIVE, MONITORING, ATTENTION, HIGH_RISK, CRITICAL, MAINTENANCE, INACTIVE)
# Statuses that may only be set manually by an administrator (S2 §12).
MANUAL_STATUSES = (MAINTENANCE, INACTIVE)


class Device(Base, TimestampMixin):
    """A physical electrical device / inspection point."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    device_type: Mapped[str] = mapped_column(String(64), default="wall_switch", nullable=False)
    # --- S2 device intelligence metadata --------------------------------
    building: Mapped[str | None] = mapped_column(String(128), nullable=True)
    floor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    room: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rated_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)  # V
    rated_current: Mapped[float | None] = mapped_column(Float, nullable=True)  # A
    installation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_maintenance_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_inspection_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    organization: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)  # legacy: active|attention|critical|inactive
    risk_level: Mapped[str] = mapped_column(String(16), default="LOW", nullable=False)  # legacy: LOW|MEDIUM|HIGH|CRITICAL
    # S2 derived operational status (computed from data; manual override admin-only)
    derived_status: Mapped[str] = mapped_column(String(32), default=ACTIVE, nullable=False)
    status_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    inspections: Mapped[list[Inspection]] = relationship(back_populates="device")
    thermal_history: Mapped[list[ThermalHistory]] = relationship(back_populates="device", order_by="ThermalHistory.timestamp")

    @property
    def inspections_count(self) -> int:
        return len(self.inspections)
