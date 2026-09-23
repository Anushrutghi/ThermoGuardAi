"""TemperatureHistory model — per-component temperature readings over time."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.component import Component


class TemperatureReading(Base, TimestampMixin):
    """One temperature reading for a component during an inspection."""

    __tablename__ = "temperature_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    component_id: Mapped[int | None] = mapped_column(ForeignKey("components.id", ondelete="CASCADE"), index=True, nullable=True)
    component_label: Mapped[str] = mapped_column(index=True, nullable=False)
    inspection_id: Mapped[int | None] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), index=True, nullable=True)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)
    ambient: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)  # rise above ambient
    reading_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(default="thermal", nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # DEMO flag — never mix

    component: Mapped[Component | None] = relationship(back_populates="temperature_readings")
