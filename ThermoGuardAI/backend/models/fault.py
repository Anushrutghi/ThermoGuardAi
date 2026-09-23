"""Fault model — a detected fault with severity classification."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.component import Component
    from backend.models.inspection import Inspection


class Fault(Base, TimestampMixin):
    """A fault found during inspection with severity and recommendation."""

    __tablename__ = "faults"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), index=True, nullable=False)
    component_id: Mapped[int | None] = mapped_column(
        ForeignKey("components.id", ondelete="SET NULL"), index=True, nullable=True
    )
    component_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fault_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), index=True, nullable=False)  # healthy|warning|high|critical
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)

    inspection: Mapped[Inspection] = relationship(back_populates="faults")
    component: Mapped[Component | None] = relationship(back_populates="faults")
