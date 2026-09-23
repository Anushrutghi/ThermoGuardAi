"""Report model — generated PDF inspection reports."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.inspection import Inspection


class Report(Base, TimestampMixin):
    """Metadata for a generated PDF report."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int | None] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    generated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    report_type: Mapped[str] = mapped_column(String(32), default="inspection")
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_version: Mapped[str | None] = mapped_column(String(32), default="1.0", nullable=True)

    # S2.1 isolation: organization is DERIVED through the inspection's device
    # (Organization → Device → Inspection → Report) — no duplicated column.
    inspection: Mapped[Inspection | None] = relationship(back_populates="reports")

    @property
    def file_name(self) -> str | None:
        """Safe display metadata — the server-side path never leaves the API."""
        if not self.file_path:
            return None
        try:
            return Path(self.file_path).name
        except (TypeError, ValueError):
            return None

    @property
    def file_available(self) -> bool:
        """S5: boolean availability — never exposes the filesystem path.

        With STORAGE_BACKEND=firestore the PDF lives in Firebase Storage and
        availability is answered by the bucket (blob existence); otherwise the
        local file is checked. A boolean is always returned — never a path.
        """
        if not self.file_path:
            return False
        from backend.core.config import get_settings

        if get_settings().storage_uses_firestore:
            from backend.firebase.storage import report_file_exists

            return report_file_exists(self.file_path)
        try:
            return Path(self.file_path).exists()
        except (TypeError, ValueError):
            return False
