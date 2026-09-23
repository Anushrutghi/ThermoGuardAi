"""Maintenance management routes (org-isolated, S2.1).

Ownership chain: Organization → Device → Inspection → Maintenance.
Organization is derived through the inspection's device — never duplicated.
Unowned legacy records (no inspection, or device-less inspection) stay
accessible to all authenticated users, matching the device-less-inspection
policy; device-linked records are strictly scoped to their organization.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, require_roles
from backend.core.exceptions import NotFoundError
from backend.db.session import get_db
from backend.models.user import User
from backend.schemas.common import Message
from backend.schemas.maintenance import MaintenanceCreate, MaintenanceList, MaintenanceOut, MaintenanceUpdate
from backend.services.isolation import (
    ensure_component_accessible,
    ensure_inspection_for_organization,
    ensure_maintenance_accessible,
)
from backend.services.maintenance_service import MaintenanceService

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.get("", response_model=MaintenanceList)
def list_maintenance(
    status: str | None = None,
    priority: str | None = None,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> MaintenanceList:
    result = MaintenanceService(db).list(status=status, priority=priority, organization=user.organization)
    return MaintenanceList(
        items=[MaintenanceOut.model_validate(r) for r in result["items"]],
        total=result["total"],
        by_status=result["by_status"],
    )


@router.get("/{record_id}", response_model=MaintenanceOut)
def get_maintenance(
    record_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> MaintenanceOut:
    """Single maintenance record lookup (org-scoped, 404 for other orgs)."""
    record = ensure_maintenance_accessible(db, record_id, user.organization)
    return MaintenanceOut.model_validate(record)


@router.post("", response_model=MaintenanceOut, status_code=201)
def create_maintenance(
    payload: MaintenanceCreate,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(require_roles("admin", "technician")),  # noqa: B008
) -> MaintenanceOut:
    # A maintenance order referencing another organization's device (via its
    # inspection) is never creatable — it looks like a not-found inspection.
    if payload.inspection_id is not None:
        ensure_inspection_for_organization(db, payload.inspection_id, user.organization)
    # S2.1 gap closed (S4): a record created with ONLY a component_id must also
    # respect organization ownership — derived Component → Panel.organization.
    if payload.component_id is not None:
        ensure_component_accessible(db, payload.component_id, user.organization)
    record = MaintenanceService(db).create(organization=user.organization, **payload.model_dump())
    return MaintenanceOut.model_validate(record)


@router.post("/{record_id}/update", response_model=MaintenanceOut)
def update_maintenance(
    record_id: int,
    payload: MaintenanceUpdate,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(require_roles("admin", "technician")),  # noqa: B008
) -> MaintenanceOut:
    ensure_maintenance_accessible(db, record_id, user.organization)
    values = payload.model_dump(exclude_none=True)
    if not values:
        raise NotFoundError("Nothing to update")
    record = MaintenanceService(db).update(record_id, **values)
    return MaintenanceOut.model_validate(record)


@router.delete("/{record_id}", response_model=Message)
def delete_maintenance(
    record_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> Message:
    ensure_maintenance_accessible(db, record_id, user.organization)
    MaintenanceService(db).delete(record_id)
    return Message(message=f"Maintenance record {record_id} deleted")
