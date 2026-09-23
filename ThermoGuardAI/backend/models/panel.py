"""Panel model — a physical electrical panel being inspected."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.component import Component
    from backend.models.inspection import Inspection


class Panel(Base, TimestampMixin):
    """A physical electrical panel / switchboard.

    S4 ownership: panels created after the upgrade carry the creator's
    organization. Legacy panels (``organization`` IS NULL) stay platform-level
    assets visible to every authenticated user — exactly like the device-less
    legacy inspection policy — so existing data is never hidden or broken.
    """

    __tablename__ = "panels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # digital map / JSON
    organization: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)  # S4 owner

    components: Mapped[list[Component]] = relationship(
        back_populates="panel", cascade="all, delete-orphan", order_by="Component.label"
    )
    inspections: Mapped[list[Inspection]] = relationship(back_populates="panel")

    def __repr__(self) -> str:
        return f"<Panel {self.code}: {self.name}>"
