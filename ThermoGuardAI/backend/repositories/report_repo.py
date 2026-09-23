"""Repository for the Report model."""
from __future__ import annotations

from sqlalchemy import select

from backend.models.report import Report
from backend.repositories.base import BaseRepository
from backend.services.isolation import report_scope_condition


class ReportRepository(BaseRepository[Report]):
    model = Report

    def list_by_inspection(self, inspection_id: int) -> list[Report]:
        stmt = (
            select(Report)
            .where(Report.inspection_id == inspection_id)
            .order_by(Report.generated_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def list_scoped(self, organization: str | None, limit: int = 100) -> list[Report]:
        """Org-scoped list (S2.1): unowned legacy reports + the caller's org.

        Organization is derived through Report → Inspection → Device, so an
        organization can only ever see its own reports plus device-less legacy
        records — never another organization's.
        """
        stmt = (
            select(Report)
            .where(report_scope_condition(organization))
            .order_by(Report.generated_at.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())
