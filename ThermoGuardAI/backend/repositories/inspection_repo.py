"""Repository for the Inspection model."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from backend.models.detection import Detection
from backend.models.fault import Fault
from backend.models.inspection import Inspection
from backend.models.panel import Panel
from backend.models.user import User
from backend.repositories.base import BaseRepository

# Columns that can be sorted by directly in SQL (computed stats are sorted in Python)
_SORTABLE = {"started_at", "ended_at", "risk_score", "component_count", "frames_processed", "status", "id"}


class InspectionRepository(BaseRepository[Inspection]):
    model = Inspection

    # ------------------------------------------------------------------
    def _base_query(self):
        """Select with user/panel eager-loaded + outer-joined, newest first.

        Outer joins are required for search filters that reference Panel/User
        columns; without them SQLAlchemy would build a cartesian product.
        """
        return (
            select(Inspection)
            .options(selectinload(Inspection.user), selectinload(Inspection.panel))
            .outerjoin(Inspection.panel)
            .outerjoin(Inspection.user)
            .order_by(Inspection.started_at.desc())
        )

    def list_filtered(
        self,
        search: str | None = None,
        status: str | None = None,
        mode: str | None = None,
        panel_id: int | None = None,
        device_id: int | None = None,
        camera_source: str | None = None,
        archived: bool | None = False,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        organization: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Inspection], int]:
        """Filtered + paginated inspection listing.

        Returns (items, total) where items have user/panel relationships loaded.
        """
        stmt = self._base_query()
        count_stmt = (
            select(func.count(Inspection.id))
            .select_from(Inspection)
            .outerjoin(Inspection.panel)
            .outerjoin(Inspection.user)
        )

        def _apply(filters: list) -> None:  # noqa: ANN001
            nonlocal stmt, count_stmt
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        filters: list = []
        if status:
            filters.append(Inspection.status == status)
        if mode:
            filters.append(Inspection.mode == mode)
        if panel_id:
            filters.append(Inspection.panel_id == panel_id)
        if device_id:
            filters.append(Inspection.device_id == device_id)
        if camera_source:
            filters.append(Inspection.camera_source == camera_source)
        # S2 isolation (strict, NULL matches NULL): a user may only see
        # inspections linked to a device of their own organization, plus
        # org-less legacy inspections not yet linked to any device.
        from backend.models.device import Device

        org_condition = Device.organization.is_(None) if organization is None else Device.organization == organization
        filters.append(
            or_(
                Inspection.device_id.is_(None),
                Inspection.device.has(org_condition),
            )
        )
        if archived is not None:
            filters.append(Inspection.archived == archived)
        if from_dt:
            filters.append(Inspection.started_at >= from_dt)
        if to_dt:
            filters.append(Inspection.started_at <= to_dt)
        if search:
            like = f"%{search.strip()}%"
            filters.append(
                or_(
                    Inspection.inspection_code.ilike(like),
                    Inspection.mode.ilike(like),
                    Inspection.camera_source.ilike(like),
                    Inspection.status.ilike(like),
                    Inspection.notes.ilike(like),
                    Panel.name.ilike(like),
                    Panel.code.ilike(like),
                    Panel.location.ilike(like),
                    User.username.ilike(like),
                )
            )
        _apply(filters)

        total = int(self.db.scalar(count_stmt) or 0)
        items = list(self.db.scalars(stmt.offset(offset).limit(limit)).all())
        return items, total

    def stats_for(self, inspection_ids: list[int]) -> dict[int, dict]:
        """Per-inspection severity counts + temperature stats (one query)."""
        if not inspection_ids:
            return {}
        rows = self.db.execute(
            select(
                Detection.inspection_id,
                Detection.health,
                func.count(),
                func.max(Detection.temperature),
                func.min(Detection.temperature),
                func.avg(Detection.temperature),
            )
            .where(Detection.inspection_id.in_(inspection_ids))
            .group_by(Detection.inspection_id, Detection.health)
        ).all()
        out: dict[int, dict] = {}
        for inspection_id, health, count, tmax, tmin, tavg in rows:
            stats = out.setdefault(
                int(inspection_id),
                {"counts": {"healthy": 0, "warning": 0, "high": 0, "critical": 0}, "max_temp": None, "min_temp": None, "avg_temp": None},
            )
            stats["counts"][str(health)] = int(count)
            if tmax is not None:
                stats["max_temp"] = max(stats["max_temp"] or -1e9, float(tmax))
            if tmin is not None:
                stats["min_temp"] = min(stats["min_temp"] or 1e9, float(tmin))
            if tavg is not None:
                stats["avg_temp"] = float(tavg)
        for stats in out.values():
            if stats["max_temp"] is None:
                stats["max_temp"] = None
                stats["min_temp"] = None
            else:
                stats["max_temp"] = round(stats["max_temp"], 1)
                stats["min_temp"] = round(stats["min_temp"], 1)
            stats["avg_temp"] = round(stats["avg_temp"], 1) if stats["avg_temp"] is not None else None
        return out

    def fault_counts_for(self, inspection_ids: list[int]) -> dict[int, int]:
        if not inspection_ids:
            return {}
        rows = self.db.execute(
            select(Fault.inspection_id, func.count())
            .where(Fault.inspection_id.in_(inspection_ids))
            .group_by(Fault.inspection_id)
        ).all()
        return {int(iid): int(c) for iid, c in rows}

    # ------------------------------------------------------------------
    def get_with_details(self, inspection_id: int) -> Inspection | None:
        stmt = (
            select(Inspection)
            .where(Inspection.id == inspection_id)
            .options(
                selectinload(Inspection.user),
                selectinload(Inspection.panel),
                selectinload(Inspection.detections),
                selectinload(Inspection.faults),
                selectinload(Inspection.incidents),
            )
        )
        return self.db.scalar(stmt)

    def list_recent(self, limit: int = 50, panel_id: int | None = None) -> list[Inspection]:
        stmt = select(Inspection).order_by(Inspection.started_at.desc()).limit(limit)
        if panel_id:
            stmt = stmt.where(Inspection.panel_id == panel_id)
        return list(self.db.scalars(stmt).all())

    def count_since(self, since: datetime) -> int:
        stmt = select(func.count()).select_from(Inspection).where(Inspection.started_at >= since)
        return int(self.db.scalar(stmt) or 0)

    def count_started_on(self, day: datetime) -> int:
        """Number of inspections started on the same calendar day (for TG- codes)."""
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        stmt = select(func.count()).select_from(Inspection).where(Inspection.started_at >= start, Inspection.started_at < end)
        return int(self.db.scalar(stmt) or 0)

    def avg_risk_since(self, since: datetime) -> float:
        stmt = select(func.avg(Inspection.risk_score)).where(Inspection.started_at >= since)
        val = self.db.scalar(stmt)
        return float(val or 0.0)

    def fault_counts_since(self, since: datetime) -> dict[str, int]:
        """Return {severity: count} of faults recorded since a timestamp."""
        stmt = (
            select(Fault.severity, func.count())
            .join(Inspection, Fault.inspection_id == Inspection.id)
            .where(Inspection.started_at >= since)
            .group_by(Fault.severity)
        )
        counts: dict[str, int] = {}
        for severity, count in self.db.execute(stmt).all():
            counts[str(severity)] = int(count)
        return counts

    def inspections_per_day(self, days: int = 30) -> list[tuple[datetime, int]]:
        since = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(func.date(Inspection.started_at), func.count())
            .where(Inspection.started_at >= since)
            .group_by(func.date(Inspection.started_at))
            .order_by(func.date(Inspection.started_at))
        )
        return [(datetime.strptime(str(day), "%Y-%m-%d"), int(count)) for day, count in self.db.execute(stmt).all()]
