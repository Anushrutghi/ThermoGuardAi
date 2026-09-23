"""Analytics routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ai.predictive.maintenance import PredictiveMaintenanceEngine
from analytics.service import AnalyticsService
from backend.api.deps import get_current_user
from backend.db.session import get_db
from backend.models.user import User
from backend.repositories.component_repo import ComponentRepository

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary")
def summary(days: int = 30, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    return AnalyticsService(db).summary(days)


@router.get("/inspections")
def inspections_per_day(days: int = 30, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    return {"items": AnalyticsService(db).inspections_per_day(days)}


@router.get("/temperatures")
def temperatures(days: int = 30, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    return {"items": AnalyticsService(db).temperature_series(days)}


@router.get("/component-health")
def component_health(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> dict:
    return {"items": AnalyticsService(db).component_health()}


@router.get("/predictive")
def predictive(panel_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:  # noqa: B008
    """Per-component failure predictions from temperature history (org-scoped)."""
    if panel_id is not None:
        # A foreign org's panel is treated as not found (S4).
        from backend.services.isolation import ensure_panel_accessible

        ensure_panel_accessible(db, panel_id, user.organization)
    components = ComponentRepository(db).list_for_panel(panel_id) if panel_id else ComponentRepository(db).list(limit=500)
    history: list[dict] = []
    for component in components:
        readings = ComponentRepository(db).temperature_history(component.id, limit=200)
        if not readings:
            continue
        history.append(
            {
                "component_id": component.id,
                "label": component.label,
                "component_type": component.component_type,
                "temperatures": [r.temperature for r in readings],
                "timestamps": [r.reading_time for r in readings],
                "ambients": [r.ambient for r in readings if r.ambient is not None] if len([r for r in readings if r.ambient is not None]) == len(readings) else None,
                "last_replaced_at": component.last_replaced_at,
            }
        )
    predictions = PredictiveMaintenanceEngine().predict(history)
    return {
        "items": [
            {
                "component_id": p.component_id,
                "label": p.label,
                "component_type": p.component_type,
                "trend_c_per_inspection": p.trend_c_per_inspection,
                "rul_hours": p.rul_hours,
                "failure_probability": p.failure_probability,
                "health": p.health,
                "recommendation": p.recommendation,
                "projected_crossing_days": p.projected_crossing_days,
                "crossing_ci_95_days": p.crossing_ci_95_days,
                "r_squared": p.r_squared,
                "trend_stderr": p.trend_stderr,
                "trend_significant": p.trend_significant,
                "data_status": p.data_status,
                "probability_status": p.probability_status,
                "assumptions": p.assumptions,
                "operating_context": p.operating_context,
                "explanation": p.explanation,
            }
            for p in predictions
        ]
    }
