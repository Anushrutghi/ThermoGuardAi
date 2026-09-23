"""Global anomaly center routes (S3).

Aggregates REAL detected anomalies (fault rows from completed inspections on
the caller's organization's devices) into a single org-scoped list. Repeated
anomalies are detected per device+kind — frames within one inspection are
never counted as separate historical anomalies (S2 §6). No data is fabricated:
only faults that actually exist in the database are returned.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user
from backend.core.exceptions import NotFoundError
from backend.db.session import get_db
from backend.models.device import Device
from backend.models.fault import Fault
from backend.models.inspection import Inspection
from backend.models.user import User
from backend.repositories.device_repo import organization_scope
from backend.services.device_analytics import DeviceAnalyticsService

router = APIRouter(prefix="/anomalies", tags=["anomalies"])


@router.get("")
def list_anomalies(
    severity: str | None = None,
    device_id: int | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Org-scoped anomaly list from real fault data (S3 §17)."""
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    analytics = DeviceAnalyticsService(db)

    # Scope to the caller's organization's devices (NULL matches NULL).
    device_ids = db.scalars(select(Device.id).where(organization_scope(user.organization))).all()
    base = (
        select(Fault, Inspection, Device)
        .join(Inspection, Fault.inspection_id == Inspection.id)
        .join(Device, Inspection.device_id == Device.id)
        .where(Inspection.status == "completed", Inspection.device_id.in_(device_ids))
    )
    if severity:
        base = base.where(Fault.severity == severity)
    if device_id is not None:
        # A device outside the caller's organization is never visible.
        if device_id not in device_ids:
            raise NotFoundError(f"Device {device_id} not found")
        base = base.where(Inspection.device_id == device_id)
    if from_date:
        base = base.where(Inspection.started_at >= from_date)
    if to_date:
        base = base.where(Inspection.started_at <= to_date)

    rows = db.execute(base.order_by(Inspection.started_at.desc(), Fault.id.desc()).limit(5000)).all()

    # Repeated-anomaly detection is computed over the FULL org-scoped fault
    # history (unaffected by the current severity/date filters), so a repeated
    # flag always reflects history — never just the visible page (S2 §6: count
    # DISTINCT inspections per device+kind; frames inside one inspection never
    # count separately).
    rep_rows = db.execute(
        select(Inspection.device_id, Fault.fault_type, func.count(func.distinct(Fault.inspection_id)))
        .join(Inspection, Fault.inspection_id == Inspection.id)
        .where(Inspection.status == "completed", Inspection.device_id.in_(device_ids))
        .group_by(Inspection.device_id, Fault.fault_type)
    ).all()
    repeated: dict[tuple[int, str], int] = {(dev_id, ft): int(cnt) for dev_id, ft, cnt in rep_rows}
    # one row per fault row is honest, but repeated flags refer to historical inspections.
    items = [
        {
            "fault_id": f.id,
            "inspection_id": insp.id,
            "inspection_code": insp.inspection_code,
            "device_id": device.id,
            "device_name": device.name,
            "occurred_at": insp.started_at.isoformat() if insp.started_at else None,
            "fault_type": f.fault_type,
            "kind": analytics.anomaly_kind(f.fault_type),
            "severity": f.severity,
            "temperature": f.temperature,
            "message": f.message,
            "recommendation": f.recommendation,
            "repeated": repeated.get((device.id, f.fault_type), 0) >= 2,
            "occurrences": repeated.get((device.id, f.fault_type), 1),
        }
        for f, insp, device in rows
    ]
    return {"items": items[offset : offset + limit], "total": len(items)}
