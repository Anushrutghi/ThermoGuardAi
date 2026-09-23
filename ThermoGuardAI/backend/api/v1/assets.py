"""Asset lifecycle management routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, require_roles
from backend.core.exceptions import NotFoundError
from backend.db.session import get_db
from backend.models.user import User
from backend.schemas.asset import (
    AssetComponentOut,
    AssetOverview,
    AssetOverviewTotals,
    ComponentAssetDetail,
    ComponentUpdate,
    PanelAssetOut,
)
from backend.services.asset_service import AssetService
from backend.services.isolation import ensure_component_accessible, ensure_panel_accessible

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/overview", response_model=AssetOverview)
def asset_overview(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> AssetOverview:  # noqa: B008
    """Per-panel lifecycle summary + platform totals (org-scoped)."""
    data = AssetService(db).overview(organization=user.organization)
    return AssetOverview(
        panels=[PanelAssetOut.model_validate(p) for p in data["panels"]],
        totals=AssetOverviewTotals.model_validate(data["totals"]),
    )


@router.get("/panels/{panel_id}", response_model=PanelAssetOut)
def panel_assets(panel_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> PanelAssetOut:  # noqa: B008
    """Panel with per-component lifecycle rows (reliability, RUL, costs…)."""
    ensure_panel_accessible(db, panel_id, user.organization)
    return PanelAssetOut.model_validate(AssetService(db).panel_assets(panel_id))


@router.get("/components/{component_id}", response_model=ComponentAssetDetail)
def component_assets(component_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> ComponentAssetDetail:  # noqa: B008
    """Full lifecycle for a single component: temperature/failure/maintenance history."""
    ensure_component_accessible(db, component_id, user.organization)
    return ComponentAssetDetail.model_validate(AssetService(db).component_detail(component_id))


@router.post("/components/{component_id}/replace", response_model=AssetComponentOut)
def record_replacement(
    component_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(require_roles("admin", "technician")),  # noqa: B008
) -> AssetComponentOut:
    """Record a component replacement (bumps counter, updates dates)."""
    ensure_component_accessible(db, component_id, user.organization)
    service = AssetService(db)
    service.record_replacement(component_id)
    return AssetComponentOut.model_validate(service.component_detail(component_id))


@router.post("/components/{component_id}/update", response_model=AssetComponentOut)
def update_component(
    component_id: int,
    payload: ComponentUpdate,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(require_roles("admin", "technician")),  # noqa: B008
) -> AssetComponentOut:
    """Update lifecycle fields (installed date, expected life, retirement…)."""
    ensure_component_accessible(db, component_id, user.organization)
    service = AssetService(db)
    values = payload.model_dump(exclude_none=True)
    if not values:
        raise NotFoundError("Nothing to update")
    service.update_component(component_id, **values)
    return AssetComponentOut.model_validate(service.component_detail(component_id))


@router.post("/components/{component_id}/retire", response_model=AssetComponentOut)
def retire_component(
    component_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> AssetComponentOut:
    """Mark a component as retired (taken out of service)."""
    ensure_component_accessible(db, component_id, user.organization)
    service = AssetService(db)
    service.update_component(component_id, retired=True)
    return AssetComponentOut.model_validate(service.component_detail(component_id))


@router.post("/components/{component_id}/activate", response_model=AssetComponentOut)
def activate_component(
    component_id: int,
    db: Session = Depends(get_db),  # noqa: B008
    user: User = Depends(require_roles("admin")),  # noqa: B008
) -> AssetComponentOut:
    """Bring a retired component back into service."""
    ensure_component_accessible(db, component_id, user.organization)
    service = AssetService(db)
    service.update_component(component_id, retired=False)
    return AssetComponentOut.model_validate(service.component_detail(component_id))
