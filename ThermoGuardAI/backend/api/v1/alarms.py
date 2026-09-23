"""Alarm routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, require_roles
from backend.core.exceptions import NotFoundError
from backend.db.session import get_db
from backend.models.alarm import Alarm
from backend.models.user import User
from backend.schemas.common import ORMModel
from backend.services.alarm_service import AlarmService
from backend.services.isolation import alarm_scope_condition, ensure_alarm_accessible


class AlarmOut(ORMModel):
    id: int
    inspection_id: int | None
    severity: str
    message: str
    source: str
    acknowledged: bool
    acknowledged_by: str | None
    media_path: str | None
    created_at: object


router = APIRouter(prefix="/alarms", tags=["alarms"])


@router.get("", response_model=list[AlarmOut])
def list_alarms(open_only: bool = False, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[AlarmOut]:  # noqa: B008
    """Org-scoped alarm list: unowned legacy + the caller's own organization."""
    service = AlarmService(db)
    if open_only:
        alarms = service.list_open(100, scope=alarm_scope_condition(user.organization))
    else:
        stmt = (
            select(Alarm)
            .where(alarm_scope_condition(user.organization))
            .order_by(Alarm.created_at.desc())
            .limit(100)
        )
        alarms = list(db.scalars(stmt).all())
    return [AlarmOut.model_validate(a) for a in alarms]


@router.post("/{alarm_id}/acknowledge", response_model=AlarmOut)
def acknowledge(alarm_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "technician"))) -> AlarmOut:  # noqa: B008
    ensure_alarm_accessible(db, alarm_id, user.organization)
    try:
        alarm = AlarmService(db).acknowledge(alarm_id, by=user.username)
    except LookupError as exc:
        raise NotFoundError(str(exc)) from exc
    return AlarmOut.model_validate(alarm)
