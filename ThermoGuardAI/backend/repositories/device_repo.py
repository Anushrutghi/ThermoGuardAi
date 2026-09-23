"""Device repository (S1 + S2) — CRUD, per-device statistics, org isolation.

S2 data isolation: every device query can be scoped to an organization. A
user may only ever see devices whose ``organization`` matches their own
(NULL matches NULL) — see ``organization_scope``.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from backend.models.device import Device
from backend.repositories.base import BaseRepository


def organization_scope(organization: str | None) -> ColumnElement[bool]:
    """Return the org-matching condition for Device rows (NULL matches NULL).

    Strict isolation: a user without an organization sees only org-less
    devices; a user with an organization sees only their own organization's
    devices. Nothing is ever visible across organizations.
    """
    if organization is None:
        return Device.organization.is_(None)
    return Device.organization == organization


class DeviceRepository(BaseRepository[Device]):
    """CRUD + stats for Device records, optionally org-scoped."""

    model = Device

    def list_all(self, organization: str | None = None, limit: int = 500) -> list[Device]:
        stmt = select(Device).where(organization_scope(organization)).order_by(Device.name.asc()).limit(limit)
        return list(self.db.scalars(stmt).all())

    def get_scoped(self, device_id: int, organization: str | None = None) -> Device | None:
        """Fetch a device only if it belongs to the caller's organization."""
        stmt = select(Device).where(Device.id == device_id, organization_scope(organization))
        return self.db.scalar(stmt)

    def count_scoped(self, organization: str | None = None) -> int:
        stmt = select(func.count()).select_from(Device).where(organization_scope(organization))
        return int(self.db.scalar(stmt) or 0)

    def inspection_stats(self, device_id: int) -> dict:
        """Aggregated inspection stats for one device (isolated per device)."""
        from backend.models.detection import Detection
        from backend.models.inspection import Inspection

        count = int(
            self.db.scalar(
                select(func.count()).select_from(Inspection).where(Inspection.device_id == device_id)
            )
            or 0
        )
        last = self.db.scalar(
            select(Inspection)
            .where(Inspection.device_id == device_id, Inspection.status == "completed")
            .order_by(Inspection.started_at.desc())
            .limit(1)
        )
        last_max_temp: float | None = None
        if last is not None:
            val = self.db.scalar(
                select(func.max(Detection.temperature)).where(Detection.inspection_id == last.id)
            )
            last_max_temp = float(val) if val is not None else None
        return {
            "inspections_count": count,
            "last_inspection_at": last.started_at if last else None,
            "last_risk_score": last.risk_score if last else None,
            "last_max_temp": last_max_temp,
            "last_thermal_simulated": last.thermal_simulated if last else None,
            "last_status": last.status if last else None,
        }
