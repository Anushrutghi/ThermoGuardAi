"""Alarm model — intelligent alarm events triggered on critical faults."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.inspection import Inspection


class Alarm(Base, TimestampMixin):
    """A triggered alarm (critical/high-risk fault)."""

    __tablename__ = "alarms"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int | None] = mapped_column(ForeignKey("inspections.id", ondelete="SET NULL"), index=True, nullable=True)
    severity: Mapped[str] = mapped_column(String(16), default="critical", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="system")
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_path: Mapped[str | None] = mapped_column(String(512), nullable=True)  # captured annotated frame / clip
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    inspection: Mapped[Inspection | None] = relationship(back_populates="alarms")
