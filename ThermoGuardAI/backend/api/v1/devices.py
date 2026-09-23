"""Device routes (S1 + S2) — CRUD, org isolation, device intelligence.

Data isolation (S2 §15): every device access is scoped to the caller's
organization. A user requesting another organization's device gets a
not-found response — never their data, never an existence leak.

S2 endpoints (all device-scoped, real DB data, never fabricated):
  GET  /devices/dashboard              — organization dashboard aggregation
  GET  /devices/{id}/history           — device history summary
  GET  /devices/{id}/inspections       — inspection history for the device
  GET  /devices/{id}/thermal-history   — per-inspection thermal summaries
  GET  /devices/{id}/anomalies         — anomaly history + repeats
  GET  /devices/{id}/health            — explainable health score
  GET  /devices/{id}/risk              — evidence-based risk
  GET  /devices/{id}/timeline          — real event timeline
  GET  /devices/{id}/maintenance       — maintenance recommendation + open orders
  GET  /devices/{id}/comparison        — current vs previous inspection
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, require_roles
from backend.core.exceptions import ForbiddenError, NotFoundError
from backend.db.session import get_db
from backend.models.device import MANUAL_STATUSES, Device
from backend.models.inspection import Inspection
from backend.models.user import User
from backend.repositories.device_repo import DeviceRepository
from backend.schemas.common import Message, Page
from backend.schemas.device import DeviceCreate, DeviceDetail, DeviceOut, DeviceUpdate
from backend.schemas.inspection import InspectionOut
from backend.services.device_analytics import DeviceAnalyticsService

router = APIRouter(prefix="/devices", tags=["devices"])


def _get_scoped_device(db: Session, device_id: int, user: User) -> Device:
    """Fetch a device, hiding existence from other organizations (S2 §15)."""
    device = DeviceRepository(db).get_scoped(device_id, organization=user.organization)
    if device is None:
        raise NotFoundError(f"Device {device_id} not found")
    return device


def _device_out(device: Device, db: Session) -> DeviceOut:
    """DeviceOut with isolated per-device statistics."""
    stats = DeviceRepository(db).inspection_stats(device.id)
    return DeviceOut(
        id=device.id,
        name=device.name,
        location=device.location,
        building=device.building,
        floor=device.floor,
        room=device.room,
        device_type=device.device_type,
        manufacturer=device.manufacturer,
        model=device.model,
        serial_number=device.serial_number,
        rated_voltage=device.rated_voltage,
        rated_current=device.rated_current,
        installation_date=device.installation_date,
        last_maintenance_date=device.last_maintenance_date,
        next_inspection_date=device.next_inspection_date,
        organization=device.organization,
        notes=device.notes,
        status=device.status,
        risk_level=device.risk_level,
        derived_status=device.derived_status,
        status_override=device.status_override,
        created_at=device.created_at,
        updated_at=device.updated_at,
        **stats,
    )


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
@router.get("", response_model=list[DeviceOut])
def list_devices(
    limit: int = 500,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> list[DeviceOut]:
    """List devices scoped to the caller's organization (S2 §15)."""
    return [_device_out(d, db) for d in DeviceRepository(db).list_all(organization=user.organization, limit=min(limit, 1000))]


@router.post("", response_model=DeviceOut, status_code=201)
def create_device(
    payload: DeviceCreate,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(require_roles("admin", "technician")),  # noqa: B008
) -> DeviceOut:
    """Create a device. It inherits the creator's organization — a user can
    never create a device inside another organization."""
    values = payload.model_dump(exclude_unset=True)
    values.pop("organization", None)  # org is always derived from the creator
    repo = DeviceRepository(db)
    device = repo.create(
        **values,
        organization=user.organization,
        status="active",
        risk_level="LOW",
        derived_status="ACTIVE",
        status_override=False,
        created_by=user.id,
    )
    db.commit()
    return _device_out(device, db)


@router.get("/dashboard")
def device_dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:  # noqa: B008
    """Organization dashboard aggregation from real DB values (S2 §14).

    Registered before /{device_id} so the path is not shadowed."""
    return DeviceAnalyticsService(db).dashboard(user)


# ---------------------------------------------------------------------------
# Single device
# ---------------------------------------------------------------------------
@router.get("/{device_id}", response_model=DeviceDetail)
def get_device(
    device_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> DeviceDetail:
    device = _get_scoped_device(db, device_id, user)
    analytics = DeviceAnalyticsService(db)
    analytics.refresh_derived_status(device)
    db.commit()
    base = _device_out(device, db)
    recent = (
        db.query(Inspection)
        .filter(Inspection.device_id == device_id, Inspection.status == "completed")
        .order_by(Inspection.started_at.desc())
        .limit(10)
        .all()
    )
    return DeviceDetail(
        **base.model_dump(),
        recent_inspections=[InspectionOut.model_validate(i) for i in recent],
        history=analytics.history(device),
    )


@router.put("/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: int,
    payload: DeviceUpdate,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(require_roles("admin", "technician")),  # noqa: B008
) -> DeviceOut:
    device = _get_scoped_device(db, device_id, user)
    values = payload.model_dump(exclude_unset=True)
    derived = values.pop("derived_status", None)
    if derived is not None:
        if derived in MANUAL_STATUSES:
            # Manual maintenance/inactive requires administrator authorization.
            if user.role != "admin":
                raise ForbiddenError(
                    "Setting MAINTENANCE or INACTIVE status requires administrator authorization"
                )
            device.derived_status = derived
            device.status_override = True
        else:
            # Non-manual values reset the override — the status is re-derived
            # from real inspection data (S2 §12).
            device.status_override = False
            device.derived_status = DeviceAnalyticsService(db).refresh_derived_status(device) or derived
    for key, value in values.items():
        setattr(device, key, value)
    db.commit()
    return _device_out(device, db)


@router.delete("/{device_id}", response_model=Message)
def delete_device(
    device_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> Message:
    device = _get_scoped_device(db, device_id, user)
    db.delete(device)
    db.commit()
    return Message(message=f"Device {device_id} deleted")


# ---------------------------------------------------------------------------
# S2 device intelligence
# ---------------------------------------------------------------------------
@router.get("/{device_id}/history")
def device_history(
    device_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Device history summary from real database data (S2 §2)."""
    device = _get_scoped_device(db, device_id, user)
    return DeviceAnalyticsService(db).history(device)


@router.get("/{device_id}/inspections", response_model=Page[InspectionOut])
def device_inspections(
    device_id: int,
    status: str | None = None,
    archived: bool | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> Page[InspectionOut]:
    """Inspection history for exactly one device (paginated, org-scoped)."""
    _get_scoped_device(db, device_id, user)
    from backend.api.v1.inspections import _summary
    from backend.repositories.inspection_repo import InspectionRepository

    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    repo = InspectionRepository(db)
    items, total = repo.list_filtered(device_id=device_id, status=status, archived=archived, limit=5000, offset=0)
    ids = [i.id for i in items]
    stats = repo.stats_for(ids)
    fault_counts = repo.fault_counts_for(ids)
    rows = [_summary(i, stats.get(i.id, {}), fault_counts.get(i.id, 0)) for i in items]
    return Page(items=rows[offset : offset + limit], total=total, limit=limit, offset=offset)


@router.get("/{device_id}/thermal-history")
def device_thermal_history(
    device_id: int,
    exclude_demo: bool = True,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Per-inspection thermal history (S2 §3). DEMO/simulated rows are clearly
    labelled and excluded by default — real analytics can rely on this."""
    _get_scoped_device(db, device_id, user)
    return DeviceAnalyticsService(db).thermal_history(device_id, exclude_demo=exclude_demo, limit=limit, offset=offset)


@router.get("/{device_id}/temperature-trend")
def device_temperature_trend(
    device_id: int,
    exclude_demo: bool = True,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Historical temperature trend per device (S2 §4). Returns an explicit
    insufficient-data message when fewer than the minimum points exist."""
    _get_scoped_device(db, device_id, user)
    return DeviceAnalyticsService(db).temperature_trend(device_id, exclude_demo=exclude_demo)


@router.get("/{device_id}/delta-trend")
def device_delta_trend(
    device_id: int,
    exclude_demo: bool = True,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """ΔT (switch vs reference) trend per device (S2 §5)."""
    _get_scoped_device(db, device_id, user)
    return DeviceAnalyticsService(db).delta_trend(device_id, exclude_demo=exclude_demo)


@router.get("/{device_id}/anomalies")
def device_anomalies(
    device_id: int,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Anomaly history and repeated-anomaly detection (S2 §6). Frames within a
    single inspection are never counted as separate historical anomalies."""
    device = _get_scoped_device(db, device_id, user)
    analytics = DeviceAnalyticsService(db)
    faults = analytics.device_faults(device.id)
    items = [
        {
            "inspection_id": f.inspection_id,
            "inspection_code": insp.inspection_code,
            "occurred_at": insp.started_at.isoformat() if insp.started_at else None,
            "fault_type": f.fault_type,
            "kind": analytics.anomaly_kind(f.fault_type),
            "severity": f.severity,
            "temperature": f.temperature,
            "message": f.message,
        }
        for f, insp in faults
    ]
    limit = min(max(limit, 1), 1000)
    offset = max(offset, 0)
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        **analytics.repeated_anomalies(device),
    }


@router.get("/{device_id}/health")
def device_health(
    device_id: int,
    exclude_demo: bool = True,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Explainable, deterministic device health score (S2 §7)."""
    device = _get_scoped_device(db, device_id, user)
    return DeviceAnalyticsService(db).health(device, exclude_demo=exclude_demo)


@router.get("/{device_id}/risk")
def device_risk(
    device_id: int,
    exclude_demo: bool = True,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Evidence-based risk level + reasoning (S2 §8)."""
    device = _get_scoped_device(db, device_id, user)
    return DeviceAnalyticsService(db).risk(device, exclude_demo=exclude_demo)


@router.get("/{device_id}/timeline")
def device_timeline(
    device_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Real event timeline from database events — never synthetic (S2 §11)."""
    device = _get_scoped_device(db, device_id, user)
    return DeviceAnalyticsService(db).timeline(device)


@router.get("/{device_id}/maintenance")
def device_maintenance(
    device_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Maintenance recommendation from actual evidence (S2 §9)."""
    device = _get_scoped_device(db, device_id, user)
    return DeviceAnalyticsService(db).maintenance(device)


@router.get("/{device_id}/comparison")
def device_comparison(
    device_id: int,
    exclude_demo: bool = True,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Current vs previous valid inspection (S2 §10)."""
    _get_scoped_device(db, device_id, user)
    return DeviceAnalyticsService(db).comparison(device_id, exclude_demo=exclude_demo)
