"""EventLog model — audit trail of system events."""
from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base, TimestampMixin


class EventLog(Base, TimestampMixin):
    """Audit / event log entry."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(16), default="info", index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[int | None] = mapped_column(nullable=True)
