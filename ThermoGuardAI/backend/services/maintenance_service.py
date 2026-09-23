"""Maintenance service — maintenance work-order management."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.exceptions import NotFoundError
from backend.models.component import Component
from backend.models.maintenance import MaintenanceRecord
from backend.repositories.base import BaseRepository
from backend.services.isolation import maintenance_scope_condition

logger = logging.getLogger(__name__)


class MaintenanceRepository(BaseRepository[MaintenanceRecord]):
    model = MaintenanceRecord

    def list_filtered(
        self,
        status: str | None = None,
        priority: str | None = None,
        organization: str | None = None,
        limit: int = 200,
    ) -> list[MaintenanceRecord]:
        """Org-scoped list (S2.1): unowned legacy records + the caller's org."""
        stmt = select(MaintenanceRecord).where(maintenance_scope_condition(organization))
        if status:
            stmt = stmt.where(MaintenanceRecord.status == status)
        if priority:
            stmt = stmt.where(MaintenanceRecord.priority == priority)
        stmt = stmt.order_by(MaintenanceRecord.created_at.desc()).limit(limit)
        return list(self.db.scalars(stmt).all())

    def by_status_counts(self, organization: str | None = None) -> dict[str, int]:
        rows = self.db.execute(
            select(MaintenanceRecord.status, func.count())
            .where(maintenance_scope_condition(organization))
            .group_by(MaintenanceRecord.status)
        ).all()
        counts = {str(s): int(c) for s, c in rows}
        for key in ("pending", "in_progress", "completed"):
            counts.setdefault(key, 0)
        return counts


class MaintenanceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.records = MaintenanceRepository(db)

    def list(self, status: str | None = None, priority: str | None = None, organization: str | None = None) -> dict:
        items = self.records.list_filtered(status=status, priority=priority, organization=organization)
        return {
            "items": items,
            "total": len(items),
            "by_status": self.records.by_status_counts(organization=organization),
        }

    def create(self, organization: str | None = None, **values) -> MaintenanceRecord:
        """Create a maintenance record.

        S4 ownership: any component the record links to (explicitly via
        ``component_id`` or resolved from ``component_label``) must belong to
        the caller's organization — derived through Component → Panel. Legacy
        components on NULL-org panels stay usable by everyone; an org-scoped
        panel's component is a not-found for every other organization.
        """
        values.setdefault("status", "pending")
        if values.get("status") == "completed":
            values.setdefault("completed_at", datetime.now(UTC))
        # resolve component link from label when a component_id isn't supplied;
        # prefer a panel-scoped match (via the inspection) so identical labels on
        # different panels don't link to the wrong asset
        component = None
        if not values.get("component_id") and values.get("component_label"):
            if values.get("inspection_id"):
                from backend.models.inspection import Inspection

                panel_id = self.db.scalar(
                    select(Inspection.panel_id).where(Inspection.id == values["inspection_id"])
                )
                if panel_id:
                    component = self.db.scalar(
                        select(Component).where(
                            Component.panel_id == panel_id,
                            Component.label == values["component_label"],
                        )
                    )
            if component is None:
                component = self.db.scalar(
                    select(Component).where(Component.label == values["component_label"]).limit(1)
                )
            if component:
                values["component_id"] = component.id
        # S4 ownership gate: the final component link (explicit or resolved from
        # a label) must belong to the caller's organization — derived through
        # Component → Panel.organization. Legacy NULL-org panels stay open.
        final_component_id = values.get("component_id")
        if final_component_id:
            from backend.models.panel import Panel

            found = self.db.get(Component, final_component_id)
            if found is None:
                raise NotFoundError(f"Component {final_component_id} not found")
            panel = self.db.get(Panel, found.panel_id)
            if panel is not None and panel.organization is not None and panel.organization != organization:
                raise NotFoundError(f"Component {final_component_id} not found")
        seq = int(self.db.scalar(select(func.count()).select_from(MaintenanceRecord)) or 0) + 1
        record = self.records.create(code=f"MT-{seq:05d}", **values)
        self.db.commit()
        logger.info("Maintenance record %s created (inspection=%s component=%s)", record.code, values.get("inspection_id"), values.get("component_id"))
        return record

    def update(self, record_id: int, **values) -> MaintenanceRecord:
        record = self.records.get(record_id)
        if record is None:
            raise NotFoundError(f"Maintenance record {record_id} not found")
        if values.get("status") == "completed":
            record.completed_at = datetime.now(UTC)
        elif values.get("status") and values["status"] != "completed":
            record.completed_at = None
        for key, value in values.items():
            if value is not None:
                setattr(record, key, value)
        self.db.commit()
        return record

    def delete(self, record_id: int) -> None:
        record = self.records.get(record_id)
        if record is None:
            raise NotFoundError(f"Maintenance record {record_id} not found")
        self.records.delete(record)
        self.db.commit()
