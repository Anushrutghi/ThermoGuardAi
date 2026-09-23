"""Panel and component routes (S4: authenticated + org-scoped).

Ownership: a panel created after the upgrade carries its creator's
organization. Legacy panels (organization NULL — the seeded PANEL-MAIN and all
pre-S4 panels) remain platform-level assets visible to every authenticated
user, so existing data is never hidden. Org-scoped panels are only visible to
their own organization; a cross-organization access returns 404.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, require_roles
from backend.core.exceptions import ConflictError
from backend.db.session import get_db
from backend.models.user import User
from backend.repositories.component_repo import ComponentRepository
from backend.repositories.panel_repo import PanelRepository
from backend.schemas.panel import ComponentCreate, ComponentOut, PanelCreate, PanelDetail, PanelOut
from backend.services.isolation import ensure_panel_accessible, panel_scope_condition

router = APIRouter(prefix="/panels", tags=["panels"])


@router.get("", response_model=list[PanelOut])
def list_panels(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),  # noqa: B008
) -> list[PanelOut]:
    """List panels: legacy (NULL-org) + the caller's own organization."""
    repo = PanelRepository(db)
    return [
        PanelOut.model_validate(p)
        for p in repo.list_scoped(panel_scope_condition(user.organization), limit=200)
    ]


@router.post("", response_model=PanelOut, status_code=201)
def create_panel(
    payload: PanelCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "technician")),  # noqa: B008
) -> PanelOut:
    """Create a panel. It inherits the creator's organization — a user can
    never create a panel inside another organization."""
    repo = PanelRepository(db)
    if repo.get_by_code(payload.code):
        raise ConflictError(f"Panel code already exists: {payload.code}")
    panel = repo.create(
        name=payload.name,
        code=payload.code,
        location=payload.location,
        description=payload.description,
        organization=user.organization,
    )
    db.commit()
    return PanelOut.model_validate(panel)


@router.get("/{panel_id}", response_model=PanelDetail)
def get_panel(
    panel_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),  # noqa: B008
) -> PanelDetail:
    panel = ensure_panel_accessible(db, panel_id, user.organization)
    components = ComponentRepository(db).list_for_panel(panel_id)
    detail = PanelDetail.model_validate(panel)
    detail.components = [ComponentOut.model_validate(c) for c in components]
    return detail


@router.post("/{panel_id}/components", response_model=ComponentOut, status_code=201)
def add_component(
    panel_id: int,
    payload: ComponentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "technician")),  # noqa: B008
) -> ComponentOut:
    panel = ensure_panel_accessible(db, panel_id, user.organization)
    component = ComponentRepository(db).create(
        panel_id=panel.id,
        label=payload.label,
        component_type=payload.component_type,
        x=payload.x,
        y=payload.y,
        w=payload.w,
        h=payload.h,
    )
    db.commit()
    return ComponentOut.model_validate(component)
