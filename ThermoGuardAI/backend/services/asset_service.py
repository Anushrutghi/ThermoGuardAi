"""Asset lifecycle service — panel & component lifecycle aggregation.

Combines temperature history, failure history, maintenance history and the
predictive AI engines (RUL / failure probability) into per-asset lifecycle
metrics: reliability score, remaining useful life, replacement tracking and
cost of repairs.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai.fault.rules import threshold_for
from ai.predictive.maintenance import PredictiveMaintenanceEngine
from backend.core.exceptions import NotFoundError
from backend.models.component import Component
from backend.models.fault import Fault
from backend.models.inspection import Inspection
from backend.models.maintenance import MaintenanceRecord
from backend.repositories.component_repo import ComponentRepository
from backend.repositories.panel_repo import PanelRepository

logger = logging.getLogger(__name__)

# Severity penalty weights for the reliability score (per fault in the window)
_SEVERITY_PENALTY = {"critical": 15.0, "high": 8.0, "warning": 3.0}
# Faults older than this no longer drag the reliability score
_FAULT_WINDOW_DAYS = 90
# Components below this reliability are considered at-risk
AT_RISK_THRESHOLD = 60.0


class AssetService:
    """Computes asset lifecycle metrics for panels and components."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.components = ComponentRepository(db)
        self.panels = PanelRepository(db)
        self.engine = PredictiveMaintenanceEngine()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _prediction_map(self, components: list[Component]) -> dict[int, dict]:
        """Run the predictive engine over a set of components → {component_id: metrics}.

        Uses a single batched query for all temperature readings.
        """
        ids = [c.id for c in components]
        readings_by_component = self.components.temperature_histories(ids, limit=200)
        history: list[dict] = []
        for component in components:
            readings = readings_by_component.get(component.id, [])
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
        predictions = self.engine.predict(history)
        return {
            p.component_id: {
                "rul_hours": p.rul_hours,
                "failure_probability": p.failure_probability,
                "health": p.health,
                "trend_c_per_inspection": p.trend_c_per_inspection,
                "recommendation": p.recommendation,
            }
            for p in predictions
            if p.component_id is not None
        }

    @staticmethod
    def _reliability(failure_probability: float, recent_faults: list[Fault], life_used_pct: float | None) -> float:
        """Reliability score 0..100 from predictive failure probability, recent
        fault severity and consumed service life."""
        score = 100.0 * (1.0 - failure_probability)
        for fault in recent_faults:
            score -= _SEVERITY_PENALTY.get(fault.severity, 0.0)
        if life_used_pct is not None:
            score -= max(0.0, (life_used_pct - 100.0))  # beyond expected life → penalty
        return round(max(0.0, min(100.0, score)), 1)

    def _recent_faults(self, component_ids: list[int]) -> dict[int, list[Fault]]:
        if not component_ids:
            return {}
        since = datetime.now(UTC) - timedelta(days=_FAULT_WINDOW_DAYS)
        rows = self.db.scalars(
            select(Fault)
            .where(Fault.component_id.in_(component_ids), Fault.created_at >= since)
            .order_by(Fault.created_at.desc())
        ).all()
        out: dict[int, list[Fault]] = {}
        for fault in rows:
            if fault.component_id is not None:
                out.setdefault(fault.component_id, []).append(fault)
        return out

    def _failure_counts(self, component_ids: list[int]) -> dict[int, int]:
        if not component_ids:
            return {}
        rows = self.db.execute(
            select(Fault.component_id, func.count())
            .where(Fault.component_id.in_(component_ids))
            .group_by(Fault.component_id)
        ).all()
        return {int(cid): int(count) for cid, count in rows}

    def _maintenance_by_component(self, component_ids: list[int]) -> dict[int, list[MaintenanceRecord]]:
        if not component_ids:
            return {}
        rows = self.db.scalars(
            select(MaintenanceRecord)
            .where(MaintenanceRecord.component_id.in_(component_ids))
            .order_by(MaintenanceRecord.created_at.desc())
        ).all()
        out: dict[int, list[MaintenanceRecord]] = {}
        for record in rows:
            if record.component_id is not None:
                out.setdefault(record.component_id, []).append(record)
        return out

    def _last_inspection_dates(self, panel_ids: list[int]) -> dict[int, datetime | None]:
        if not panel_ids:
            return {}
        rows = self.db.execute(
            select(Inspection.panel_id, func.max(Inspection.started_at))
            .where(Inspection.panel_id.in_(panel_ids), Inspection.status == "completed")
            .group_by(Inspection.panel_id)
        ).all()
        return {int(pid): ts for pid, ts in rows}

    # ------------------------------------------------------------------
    # overview
    # ------------------------------------------------------------------
    def overview(self, organization: str | None = None) -> dict:
        """Per-panel lifecycle summary + totals (org-scoped, S4).

        Legacy NULL-org panels plus the caller's own organization — a foreign
        organization's panels are never aggregated into these totals.
        """
        from backend.services.isolation import panel_scope_condition

        panels = self.panels.list_scoped(panel_scope_condition(organization), limit=200)
        panel_ids = [p.id for p in panels]
        last_insp = self._last_inspection_dates(panel_ids)

        out: list[dict] = []
        totals = {
            "panels": len(panels),
            "components": 0,
            "active_components": 0,
            "at_risk_components": 0,
            "open_maintenance": 0,
            "total_repair_cost": 0.0,
            "avg_reliability": 0.0,
        }
        reliabilities: list[float] = []

        for panel in panels:
            components = self.components.list_for_panel(panel.id)
            if not components:
                out.append(self._panel_overview(panel, [], last_insp.get(panel.id)))
                continue
            component_stats = self._component_stats(components)
            panel_row = self._panel_overview(panel, component_stats, last_insp.get(panel.id))
            out.append(panel_row)
            totals["components"] += panel_row["component_count"]
            totals["active_components"] += panel_row["active_components"]
            totals["at_risk_components"] += panel_row["at_risk_components"]
            totals["open_maintenance"] += panel_row["open_maintenance"]
            totals["total_repair_cost"] += panel_row["total_repair_cost"]
            if panel_row["component_count"]:
                reliabilities.append(panel_row["avg_reliability"])

        totals["avg_reliability"] = round(sum(reliabilities) / len(reliabilities), 1) if reliabilities else 0.0
        return {"panels": out, "totals": totals}

    def _component_stats(self, components: list[Component]) -> list[dict]:
        """Per-component lifecycle rows (shared by overview + panel detail)."""
        ids = [c.id for c in components]
        predictions = self._prediction_map(components)
        recent_faults = self._recent_faults(ids)
        failure_counts = self._failure_counts(ids)
        maintenance = self._maintenance_by_component(ids)

        rows = []
        for component in components:
            pred = predictions.get(component.id, {})
            faults = recent_faults.get(component.id, [])
            records = maintenance.get(component.id, [])
            open_mt = [r for r in records if r.status in ("pending", "in_progress")]
            completed_cost = sum(r.cost for r in records if r.status == "completed" and r.cost)
            reliability = self._reliability(
                pred.get("failure_probability", 0.0),
                faults,
                component.life_used_pct,
            )
            rows.append(
                {
                    "id": component.id,
                    "panel_id": component.panel_id,
                    "code": component.code,
                    "label": component.label,
                    "component_type": component.component_type,
                    "installed_at": component.installed_at,
                    "expected_life_years": component.expected_life_years,
                    "replacement_count": component.replacement_count,
                    "last_replaced_at": component.last_replaced_at,
                    "retired": component.retired,
                    "age_years": component.age_years,
                    "life_used_pct": component.life_used_pct,
                    "reliability_score": reliability,
                    "health": pred.get("health", "healthy"),
                    "rul_hours": pred.get("rul_hours"),
                    "failure_probability": pred.get("failure_probability", 0.0),
                    "trend_c_per_inspection": pred.get("trend_c_per_inspection", 0.0),
                    "recommendation": pred.get("recommendation", ""),
                    "failure_count": failure_counts.get(component.id, 0),
                    "open_maintenance": len(open_mt),
                    "total_repair_cost": round(completed_cost, 2),
                    "last_maintenance_at": records[0].completed_at if records and records[0].completed_at else (records[0].created_at if records else None),
                }
            )
        return rows

    def _panel_overview(self, panel, component_rows: list[dict], last_inspection_at) -> dict:  # noqa: ANN001
        active = [r for r in component_rows if not r["retired"]]
        at_risk = [r for r in component_rows if not r["retired"] and r["reliability_score"] < AT_RISK_THRESHOLD]
        reliabilities = [r["reliability_score"] for r in component_rows]
        open_mt = sum(r["open_maintenance"] for r in component_rows)
        total_cost = round(sum(r["total_repair_cost"] for r in component_rows), 2)
        return {
            "id": panel.id,
            "name": panel.name,
            "code": panel.code,
            "location": panel.location,
            "component_count": len(component_rows),
            "active_components": len(active),
            "at_risk_components": len(at_risk),
            "avg_reliability": round(sum(reliabilities) / len(reliabilities), 1) if reliabilities else 0.0,
            "open_maintenance": open_mt,
            "total_repair_cost": total_cost,
            "last_inspection_at": last_inspection_at,
            "components": component_rows,
        }

    # ------------------------------------------------------------------
    # panel detail
    # ------------------------------------------------------------------
    def panel_assets(self, panel_id: int) -> dict:
        panel = self.panels.get(panel_id)
        if panel is None:
            raise NotFoundError(f"Panel {panel_id} not found")
        components = self.components.list_for_panel(panel_id)
        component_stats = self._component_stats(components) if components else []
        last_insp = self._last_inspection_dates([panel_id]).get(panel_id)
        return self._panel_overview(panel, component_stats, last_insp)

    # ------------------------------------------------------------------
    # component detail
    # ------------------------------------------------------------------
    def component_detail(self, component_id: int) -> dict:
        component = self.components.get(component_id)
        if component is None:
            raise NotFoundError(f"Component {component_id} not found")
        rows = self._component_stats([component])
        row = rows[0]

        readings = self.components.temperature_histories([component_id], limit=500).get(component_id, [])
        temperature_series = [
            {"time": r.reading_time.isoformat(), "temp": round(r.temperature, 2),
             "ambient": r.ambient, "inspection_id": r.inspection_id}
            for r in readings
        ]
        temperatures = [r.temperature for r in readings]
        limit_c = threshold_for(component.component_type)[2]

        failures = self.db.scalars(
            select(Fault)
            .where(Fault.component_id == component_id)
            .order_by(Fault.created_at.desc())
            .limit(50)
        ).all()
        maintenance = self.db.scalars(
            select(MaintenanceRecord)
            .where(MaintenanceRecord.component_id == component_id)
            .order_by(MaintenanceRecord.created_at.desc())
            .limit(50)
        ).all()

        return {
            **row,
            "panel_name": component.panel.name if component.panel else None,
            "panel_code": component.panel.code if component.panel else None,
            "panel_location": component.panel.location if component.panel else None,
            "limit_c": limit_c,
            "temperature_series": temperature_series,
            "temperature_stats": {
                "max": round(max(temperatures), 1) if temperatures else None,
                "min": round(min(temperatures), 1) if temperatures else None,
                "avg": round(sum(temperatures) / len(temperatures), 1) if temperatures else None,
                "readings": len(temperatures),
            },
            "failures": [
                {
                    "id": f.id,
                    "fault_type": f.fault_type,
                    "severity": f.severity,
                    "confidence": f.confidence,
                    "temperature": f.temperature,
                    "message": f.message,
                    "recommendation": f.recommendation,
                    "created_at": f.created_at,
                    "inspection_code": f.inspection.inspection_code if f.inspection else None,
                }
                for f in failures
            ],
            "maintenance": [
                {
                    "id": m.id,
                    "code": m.code,
                    "status": m.status,
                    "priority": m.priority,
                    "fault_type": m.fault_type,
                    "cost": m.cost,
                    "assigned_to": m.assigned_to,
                    "deadline": m.deadline,
                    "completed_at": m.completed_at,
                    "created_at": m.created_at,
                }
                for m in maintenance
            ],
        }

    # ------------------------------------------------------------------
    # lifecycle actions
    # ------------------------------------------------------------------
    def record_replacement(self, component_id: int) -> Component:
        """Record a replacement: bump the counter, set dates, un-retire."""
        component = self.components.get(component_id)
        if component is None:
            raise NotFoundError(f"Component {component_id} not found")
        from datetime import date

        today = date.today()
        component.replacement_count += 1
        component.last_replaced_at = today
        component.installed_at = today
        component.retired = False
        self.db.commit()
        logger.info("Replacement recorded for component %s (%s)", component.code, component.label)
        return component

    def update_component(self, component_id: int, **values) -> Component:
        """Update lifecycle fields (installed_at, expected_life_years, retired…)."""
        component = self.components.get(component_id)
        if component is None:
            raise NotFoundError(f"Component {component_id} not found")
        allowed = {"installed_at", "expected_life_years", "retired", "last_replaced_at"}
        for key, value in values.items():
            if key in allowed and value is not None:
                setattr(component, key, value)
        self.db.commit()
        return component
