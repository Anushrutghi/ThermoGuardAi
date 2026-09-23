"""User model (auth + RBAC)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.inspection import Inspection


class User(Base, TimestampMixin):
    """Application user with role-based access control."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="viewer", nullable=False)  # admin|technician|viewer
    # S2 data isolation: every user belongs to one organization. A user may
    # only see devices whose organization matches their own (NULL matches NULL).
    organization: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    inspections: Mapped[list[Inspection]] = relationship(back_populates="user")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_technician(self) -> bool:
        return self.role in ("admin", "technician")
