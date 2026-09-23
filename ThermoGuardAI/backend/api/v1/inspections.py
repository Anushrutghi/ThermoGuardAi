"""Inspection routes."""
from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, require_roles
from backend.core.exceptions import AppError, NotFoundError
from backend.db.session import get_db
from backend.models.alarm import Alarm
from backend.models.report import Report
from backend.models.temperature_history import TemperatureReading
from backend.models.user import User
from backend.schemas.common import Message, Page
from backend.schemas.inspection import (
    AlarmOut,
    DetectionOut,
    FaultOut,
    IncidentOut,
    InspectionDetail,
    InspectionOut,
    InspectionStart,
    InspectionStats,
    InspectionStop,
    SeverityCounts,
    TemperatureStats,
)
from backend.services.inspection_service import InspectionService
from backend.services.isolation import ensure_inspection_for_organization

router = APIRouter(prefix="/inspections", tags=["inspections"])

_SORTABLE = {"started_at", "ended_at", "risk_score", "component_count", "frames_processed", "status", "id"}


def _summary(inspection, stats: dict, faults_count: int) -> InspectionOut:  # noqa: ANN001
    """Build a history-table row from an ORM inspection + aggregated stats."""
    return InspectionOut(
        id=inspection.id,
        user_id=inspection.user_id,
        panel_id=inspection.panel_id,
        device_id=inspection.device_id,
        mode=inspection.mode,
        camera_source=inspection.camera_source,
        status=inspection.status,
        started_at=inspection.started_at,
        ended_at=inspection.ended_at,
        frames_processed=inspection.frames_processed,
        avg_fps=inspection.avg_fps,
        risk_score=inspection.risk_score,
        component_count=inspection.component_count,
        notes=inspection.notes,
        inspection_code=inspection.inspection_code,
        software_version=inspection.software_version,
        model_version=inspection.model_version,
        thermal_source=inspection.thermal_source,
        thermal_simulated=inspection.thermal_simulated,
        archived=inspection.archived,
        health_score=inspection.health_score,
        duration_s=inspection.duration_seconds,
        inspector=inspection.inspector_name,
        panel_name=inspection.panel_name,
        panel_code=inspection.panel_code,
        panel_location=inspection.panel_location,
        device_name=inspection.device_name,
        device_location=inspection.device_location,
        original_image_path=inspection.original_image_path,
        annotated_image_path=inspection.annotated_image_path,
        thermal_image_path=inspection.thermal_image_path,
        counts=SeverityCounts(**(stats.get("counts") or {})),
        temperature_stats=TemperatureStats(
            max_temp=stats.get("max_temp"),
            min_temp=stats.get("min_temp"),
            avg_temp=stats.get("avg_temp"),
        ),
        faults_count=faults_count,
    )


def _sort_key(item: InspectionOut, sort: str) -> object:
    if sort == "max_temp":
        return item.temperature_stats.max_temp if item.temperature_stats.max_temp is not None else -1e9
    if sort == "health_score":
        return item.health_score
    if sort == "faults_count":
        return item.faults_count
    if sort == "inspection_code":
        return item.inspection_code or ""
    return getattr(item, sort, 0) if sort in _SORTABLE else item.started_at


@router.post("", response_model=InspectionOut, status_code=201)
def start_inspection(        payload: InspectionStart, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "technician"))) -> InspectionOut:  # noqa: B008
    inspection = InspectionService(db).start_inspection(
        user_id=user.id,
        mode=payload.mode,
        panel_id=payload.panel_id,
        device_id=payload.device_id,
        camera_source=payload.camera_source,
        notes=payload.notes,
        organization=user.organization,
    )
    return InspectionOut.model_validate(inspection)


@router.get("", response_model=Page[InspectionOut])
def list_inspections(
    search: str | None = None,
    status: str | None = None,
    mode: str | None = None,
    panel_id: int | None = None,
    device_id: int | None = None,
    camera_source: str | None = None,
    archived: bool = False,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    sort: str = "started_at",
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> Page[InspectionOut]:
    """History table with search, filters, sorting and pagination."""
    service = InspectionService(db)
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)

    # Fetch all matching rows (capped) so sorting by computed stats (max temp,
    # health score, fault count) stays correct before pagination.
    items, total = service.inspections.list_filtered(
        search=search,
        status=status,
        mode=mode,
        panel_id=panel_id,
        device_id=device_id,
        camera_source=camera_source,
        archived=archived,
        from_dt=from_date,
        to_dt=to_date,
        organization=user.organization,
        limit=5000,
        offset=0,
    )
    ids = [i.id for i in items]
    stats = service.inspections.stats_for(ids)
    fault_counts = service.inspections.fault_counts_for(ids)

    rows = [_summary(i, stats.get(i.id, {}), fault_counts.get(i.id, 0)) for i in items]
    reverse = order.lower() == "desc"
    rows.sort(key=lambda r: _sort_key(r, sort), reverse=reverse)
    page = rows[offset : offset + limit]
    return Page(items=page, total=total, limit=limit, offset=offset)


@router.get("/export")
def export_inspections(
    search: str | None = None,
    status: str | None = None,
    mode: str | None = None,
    panel_id: int | None = None,
    device_id: int | None = None,
    camera_source: str | None = None,
    archived: bool = False,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(require_roles("admin", "technician")),  # noqa: B008
) -> StreamingResponse:
    """Export the (filtered) inspection history as CSV."""
    service = InspectionService(db)
    items, _ = service.inspections.list_filtered(
        search=search,
        status=status,
        mode=mode,
        panel_id=panel_id,
        device_id=device_id,
        camera_source=camera_source,
        archived=archived,
        from_dt=from_date,
        to_dt=to_date,
        organization=user.organization,
        limit=5000,
        offset=0,
    )
    stats = service.inspections.stats_for([i.id for i in items])

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Inspection ID", "Date & Time", "Inspector", "Camera Source", "Panel", "Location",
            "Status", "Mode", "Frames", "Components", "Healthy", "Warning", "High", "Critical",
            "Max Temp (°C)", "Health Score (%)", "Risk Score", "Duration (s)",
            "Software Version", "Model Version",
        ]
    )
    for i in items:
        s = stats.get(i.id, {})
        counts = s.get("counts") or {}
        writer.writerow(
            [
                i.inspection_code or str(i.id),
                i.started_at.isoformat(),
                i.inspector_name or "",
                i.camera_source,
                i.panel_name or "",
                i.panel_location or "",
                i.status,
                i.mode,
                i.frames_processed,
                i.component_count,
                counts.get("healthy", 0),
                counts.get("warning", 0),
                counts.get("high", 0),
                counts.get("critical", 0),
                s.get("max_temp", ""),
                i.health_score,
                i.risk_score,
                round(i.duration_seconds, 1),
                i.software_version or "",
                i.model_version or "",
            ]
        )
    filename = f"inspections_{datetime.now(UTC):%Y%m%d_%H%M}.csv"
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stats", response_model=InspectionStats)
def inspection_stats(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> InspectionStats:  # noqa: B008
    service = InspectionService(db)
    from datetime import timedelta

    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    total = service.inspections.count()
    today = service.inspections.count_since(today_start)
    open_alarms = service.alarm_service.alarms.count(acknowledged=False)
    avg_risk = service.inspections.avg_risk_since(now - timedelta(days=30))
    severity = service.inspections.fault_counts_since(now - timedelta(days=30))
    return InspectionStats(
        total=total,
        today=today,
        open_alarms=open_alarms,
        avg_risk=round(avg_risk, 1),
        by_severity=severity,
    )


@router.get("/{inspection_id}", response_model=InspectionDetail)
def get_inspection(inspection_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> InspectionDetail:  # noqa: B008
    service = InspectionService(db)
    inspection = service.get(inspection_id)
    # S2 isolation (strict, NULL matches NULL): an inspection linked to another
    # organization's device is treated as not found.
    if inspection.device is not None and inspection.device.organization != user.organization:
        raise NotFoundError(f"Inspection {inspection_id} not found")
    stats = service.inspections.stats_for([inspection.id]).get(inspection.id, {})
    fault_counts = service.inspections.fault_counts_for([inspection.id]).get(inspection.id, 0)

    temp_rows = db.execute(
        select(TemperatureReading.reading_time, TemperatureReading.component_label, TemperatureReading.temperature)
        .where(TemperatureReading.inspection_id == inspection_id)
        .order_by(TemperatureReading.reading_time.asc())
        .limit(2000)
    ).all()
    temperature_series = [
        {"time": t.isoformat(), "label": label, "temp": round(temp, 2)} for t, label, temp in temp_rows
    ]

    alarms = db.execute(
        select(Alarm).where(Alarm.inspection_id == inspection_id).order_by(Alarm.created_at.asc()).limit(50)
    ).scalars().all()
    report_ts = db.scalar(
        select(Report.generated_at)
        .where(Report.inspection_id == inspection_id)
        .order_by(Report.generated_at.desc())
        .limit(1)
    )

    # Inject stable component codes (B1, R2…) into detections
    component_codes: dict[int, str] = {}
    if inspection.panel_id:
        from backend.repositories.component_repo import ComponentRepository

        for component in ComponentRepository(db).list_for_panel(inspection.panel_id):
            component_codes[component.id] = component.code or component.label
    detections_out: list[DetectionOut] = []
    for det in inspection.detections:
        item = DetectionOut.model_validate(det)
        item.component_code = component_codes.get(det.component_id or -1)
        detections_out.append(item)

    base = _summary(inspection, stats, fault_counts)
    return InspectionDetail(
        **base.model_dump(),
        detections=detections_out,
        faults=[FaultOut.model_validate(f) for f in inspection.faults],
        incidents=[IncidentOut.model_validate(inc) for inc in inspection.incidents],
        alarms=[AlarmOut.model_validate(a) for a in alarms],
        temperature_series=temperature_series,
        report_generated_at=report_ts,
    )


@router.post("/{inspection_id}/archive", response_model=InspectionOut)
def archive_inspection(inspection_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "technician"))) -> InspectionOut:  # noqa: B008
    service = InspectionService(db)
    ensure_inspection_for_organization(db, inspection_id, user.organization)
    inspection = service.inspections.get_or_raise(inspection_id)
    service.inspections.update(inspection, archived=True)
    db.commit()
    return InspectionOut.model_validate(inspection)


@router.post("/{inspection_id}/unarchive", response_model=InspectionOut)
def unarchive_inspection(inspection_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "technician"))) -> InspectionOut:  # noqa: B008
    service = InspectionService(db)
    ensure_inspection_for_organization(db, inspection_id, user.organization)
    inspection = service.inspections.get_or_raise(inspection_id)
    service.inspections.update(inspection, archived=False)
    db.commit()
    return InspectionOut.model_validate(inspection)


@router.delete("/{inspection_id}", response_model=Message)
def delete_inspection(inspection_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))) -> Message:  # noqa: B008
    service = InspectionService(db)
    ensure_inspection_for_organization(db, inspection_id, user.organization)
    inspection = service.inspections.get(inspection_id)
    if inspection is None:
        raise NotFoundError(f"Inspection {inspection_id} not found")
    db.delete(inspection)
    db.commit()
    return Message(message=f"Inspection {inspection_id} deleted")


@router.post("/{inspection_id}/frame", response_model=dict)
async def process_uploaded_frame(
    inspection_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "technician")),  # noqa: B008
) -> dict:
    """Upload a single frame (JPEG/PNG) to run through the pipeline."""
    ensure_inspection_for_organization(db, inspection_id, user.organization)
    import cv2
    import numpy as np

    content = await file.read()
    arr = np.frombuffer(content, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise AppError("Could not decode uploaded image")
    return InspectionService(db).process_frame(inspection_id, frame)


@router.post("/{inspection_id}/stop", response_model=InspectionOut)
def stop_inspection(inspection_id: int, payload: InspectionStop | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "technician"))) -> InspectionOut:  # noqa: B008
    ensure_inspection_for_organization(db, inspection_id, user.organization)
    return InspectionOut.model_validate(
        InspectionService(db).stop_inspection(inspection_id, payload.notes if payload else None)
    )


@router.post("/{inspection_id}/abort", response_model=InspectionOut)
def abort_inspection(inspection_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "technician"))) -> InspectionOut:  # noqa: B008
    ensure_inspection_for_organization(db, inspection_id, user.organization)
    return InspectionOut.model_validate(InspectionService(db).abort_inspection(inspection_id))
