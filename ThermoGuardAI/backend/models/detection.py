"""Detection model — an object detected during an inspection frame."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.inspection import Inspection


class Detection(Base, TimestampMixin):
    """A single object detection (component) within an inspection."""

    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), index=True, nullable=False)
    component_id: Mapped[int | None] = mapped_column(ForeignKey("components.id", ondelete="SET NULL"), nullable=True)
    label: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    bbox: Mapped[str] = mapped_column(Text, nullable=False)  # JSON [x1, y1, x2, y2]
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    health: Mapped[str] = mapped_column(String(16), default="healthy", nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, default=0)
    frame_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    inspection: Mapped[Inspection] = relationship(back_populates="detections")
