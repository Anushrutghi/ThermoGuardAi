"""S2 device analytics service — device intelligence over real database data.

Every method reads only real persisted rows (inspections, thermal history,
faults, alarms, incidents, maintenance) and computes results deterministically.
Nothing is fabricated: when data is missing the response says so explicitly
("Insufficient inspection data" / "Insufficient historical data for trend
analysis." / "Insufficient data to calculate device health.").

DEMO/SIMULATED honesty: thermal analytics default to excluding simulated
(simulated=True) rows; the endpoints expose how many simulated rows were
excluded so the caller always knows what the numbers are based on.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from analytics.health_engine import HealthInputs, compute_health
from analytics.recommendations import maintenance_recommendation
from analytics.risk_engine import RiskInputs, compute_risk
from analytics.trends import classify_delta_trend, classify_temperature_trend
from backend.core.config import get_settings
from backend.models.device import (
    ACTIVE,
    ATTENTION,
    CRITICAL,
    HIGH_RISK,
    INACTIVE,
    MAINTENANCE,
    MONITORING,
    Device,
)
from backend.models.fault import Fault
from backend.models.inspection import Inspection
from backend.models.maintenance import MaintenanceRecord
from backend.models.thermal_history import ThermalHistory
from backend.repositories.device_repo import organization_scope

if TYPE_CHECKING:
    from backend.models.user import User

logger = logging.getLogger(__name__)

# Anomaly fault types raised by the switch-first flow (one fault per
# inspection — frames within one inspection are never separate anomalies).
_ANOMALY_KINDS = {
    "elevated_temperature": "slight thermal elevation",
    "thermal_anomaly": "localized thermal anomaly",
    "critical_thermal_anomaly": "critical thermal anomaly",
}

RISK_LEVELS = {"NORMAL": 1, "ELEVATED": 2, "ABNORMAL": 3, "CRITICAL": 4}


class DeviceAnalyticsService:
    """Analytics for one device (or, for the dashboard, an organization)."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        # Per-request memoization: history()/health()/risk()/maintenance() all
        # share the same raw queries; each is executed at most once per request
        # (S2 §17 — never re-load thermal measurements or faults repeatedly).
        self._cache: dict[str, object] = {}

    def _cached(self, key: str, factory):  # noqa: ANN001
        if key not in self._cache:
            self._cache[key] = factory()
        return self._cache[key]

    # ------------------------------------------------------------------
    # Queries (memoized per service instance / request)
    # ------------------------------------------------------------------
    def completed_inspections(self, device_id: int) -> list[Inspection]:
        def _load() -> list[Inspection]:
            stmt = (
                select(Inspection)
                .where(Inspection.device_id == device_id, Inspection.status == "completed")
                .order_by(Inspection.started_at.asc())
            )
            return list(self.db.scalars(stmt).all())

        return self._cached(f"insp:{device_id}", _load)  # type: ignore[return-value]

    def thermal_rows(self, device_id: int, exclude_demo: bool = True) -> list[ThermalHistory]:
        def _load() -> list[ThermalHistory]:
            stmt = select(ThermalHistory).where(ThermalHistory.device_id == device_id).order_by(ThermalHistory.timestamp.asc())
            if exclude_demo:
                stmt = stmt.where(ThermalHistory.simulated.is_(False))
            return list(self.db.scalars(stmt).all())

        return self._cached(f"th:{device_id}:{exclude_demo}", _load)  # type: ignore[return-value]

    def device_faults(self, device_id: int) -> list[tuple[Fault, Inspection]]:
        """Faults of a device joined with their inspection, oldest first.

        Memoized per request — never loaded more than once per request.
        """

        def _load() -> list[tuple[Fault, Inspection]]:
            rows = self.db.execute(
                select(Fault, Inspection)
                .join(Inspection, Fault.inspection_id == Inspection.id)
                .where(Inspection.device_id == device_id, Inspection.status == "completed")
                .order_by(Inspection.started_at.asc(), Fault.id.asc())
            ).all()
            return [(f, i) for f, i in rows]

        return self._cached(f"faults:{device_id}", _load)  # type: ignore[return-value]

    def anomaly_kind(self, fault_type: str) -> str:
        """Human label for an anomaly fault type."""
        return _ANOMALY_KINDS.get(fault_type, fault_type.replace("_", " "))

    # ------------------------------------------------------------------
    # 1. Device history (§2)
    # ------------------------------------------------------------------
    def history(self, device: Device) -> dict:
        inspections = self.completed_inspections(device.id)
        # Honesty (§19): temperature aggregations use REAL measurements only;
        # simulated (DEMO) rows are counted separately and never mixed in.
        rows = self.thermal_rows(device.id, exclude_demo=True)
        all_rows = self.thermal_rows(device.id, exclude_demo=False)
        faults = self.device_faults(device.id)
        anomaly_faults = [f for f, _ in faults if f.fault_type in _ANOMALY_KINDS]

        if not inspections:
            return {
                "device_id": device.id,
                "name": device.name,
                "derived_status": device.derived_status,
                "total_inspections": 0,
                "anomaly_count": 0,
                "repeated_anomaly_kinds": 0,
                "current_risk": None,
                "current_health": None,
                "maintenance_recommendation": None,
                "note": "Insufficient inspection data",
            }

        first = inspections[0]
        latest = inspections[-1]
        latest_row = rows[-1] if rows else None
        latest_fault = anomaly_faults[-1] if anomaly_faults else None
        max_temp = max((r.max_temp for r in rows if r.max_temp is not None), default=None)
        avg_temps = [r.avg_temp for r in rows if r.avg_temp is not None]
        avg_temp = float(sum(avg_temps) / len(avg_temps)) if avg_temps else None

        repeated = self.repeated_anomalies(device)
        risk = self.risk(device)
        health = self.health(device)
        maintenance = self.maintenance(device)
        anomaly_count = repeated["total_anomalies"]

        return {
            "device_id": device.id,
            "name": device.name,
            "derived_status": self.refresh_derived_status(device),
            "total_inspections": len(inspections),
            "first_inspection": first.started_at.isoformat() if first.started_at else None,
            "latest_inspection": latest.started_at.isoformat() if latest.started_at else None,
            "latest_temperature": round(latest_row.max_temp, 2) if latest_row and latest_row.max_temp is not None else None,
            "max_temperature": round(max_temp, 2) if max_temp is not None else None,
            "avg_temperature": round(avg_temp, 2) if avg_temp is not None else None,
            "latest_thermal_delta": round(latest_row.delta, 2) if latest_row and latest_row.delta is not None else None,
            "latest_anomaly": latest_fault.fault_type if latest_fault else None,
            "anomaly_count": anomaly_count,
            "repeated_anomaly_kinds": repeated["repeated_count"],
            "current_risk": risk.get("level"),
            "current_health": health.get("score"),
            "maintenance_recommendation": maintenance.get("recommendation"),
            "note": None,
            # DEMO disclosure: temperatures above are real-only
            "thermal_history_total": len(all_rows),
            "thermal_simulated_count": sum(1 for r in all_rows if r.simulated),
            "simulated_excluded_from_temperatures": bool(all_rows and any(r.simulated for r in all_rows)),
        }

    # ------------------------------------------------------------------
    # 3. Thermal history (§3)
    # ------------------------------------------------------------------
    def thermal_history(self, device_id: int, exclude_demo: bool = True, limit: int = 200, offset: int = 0) -> dict:
        base = select(ThermalHistory).where(ThermalHistory.device_id == device_id)
        total = int(self.db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        simulated_total = int(
            self.db.scalar(
                select(func.count())
                .select_from(ThermalHistory)
                .where(ThermalHistory.device_id == device_id, ThermalHistory.simulated.is_(True))
            )
            or 0
        )
        stmt = base.order_by(ThermalHistory.timestamp.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 1000))
        if exclude_demo:
            stmt = stmt.where(ThermalHistory.simulated.is_(False))
        rows = list(self.db.scalars(stmt).all())
        return {
            "items": [r.to_dict() for r in rows],
            "total": total,
            "exclude_demo": exclude_demo,
            "simulated_excluded": simulated_total if exclude_demo else 0,
            "note": "Insufficient thermal history data." if not rows else None,
        }

    # ------------------------------------------------------------------
    # 4–5. Trends (§4 temperature trend, §5 ΔT trend)
    # ------------------------------------------------------------------
    def temperature_trend(self, device_id: int, exclude_demo: bool = True) -> dict:
        rows = self.thermal_rows(device_id, exclude_demo=exclude_demo)
        temps = [r.max_temp for r in rows if r.max_temp is not None]
        s = self.settings
        result = classify_temperature_trend(
            temps,
            min_points=s.analytics_trend_min_points,
            decreasing_c=s.analytics_trend_decreasing_c,
            stable_c=s.analytics_trend_stable_c,
            slightly_c=s.analytics_trend_slightly_c,
            increasing_c=s.analytics_trend_increasing_c,
        )
        return {
            "status": result.status,
            "slope_c_per_inspection": result.slope_c_per_inspection,
            "points": [
                {"timestamp": r.timestamp.isoformat() if r.timestamp else None, "temperature": round(r.max_temp, 2), "simulated": r.simulated}
                for r in rows
                if r.max_temp is not None
            ],
            "insufficient": result.insufficient,
            "message": result.message,
        }

    def delta_trend(self, device_id: int, exclude_demo: bool = True) -> dict:
        rows = self.thermal_rows(device_id, exclude_demo=exclude_demo)
        deltas = [r.delta for r in rows if r.delta is not None]
        result = classify_delta_trend(deltas, min_points=self.settings.analytics_trend_min_points, threshold_c=self.settings.analytics_delta_threshold_c)
        return {
            "status": result.status,
            "slope_c_per_delta": result.slope_c_per_delta,
            "points": [
                {"timestamp": r.timestamp.isoformat() if r.timestamp else None, "delta": round(r.delta, 2), "simulated": r.simulated}
                for r in rows
                if r.delta is not None
            ],
            "insufficient": result.insufficient,
            "message": result.message,
        }

    # ------------------------------------------------------------------
    # 6. Repeated anomalies (§6)
    # ------------------------------------------------------------------
    def repeated_anomalies(self, device: Device) -> dict:
        faults = self.device_faults(device.id)
        anomaly_faults = [f for f, _ in faults if f.fault_type in _ANOMALY_KINDS]
        by_kind: dict[str, list[int]] = {}
        for f in anomaly_faults:
            by_kind.setdefault(f.fault_type, [])
            if f.inspection_id not in by_kind[f.fault_type]:
                by_kind[f.fault_type].append(f.inspection_id)

        repeated_kinds = [{"kind": _ANOMALY_KINDS[ft], "fault_type": ft, "count": len(ids), "inspections": ids} for ft, ids in by_kind.items() if len(ids) >= 2]
        if repeated_kinds:
            message = "Repeated localized thermal anomaly detected." if any(k["fault_type"] == "thermal_anomaly" for k in repeated_kinds) else "Repeated thermal anomaly detected."
        else:
            message = "No repeated anomalies detected."
        return {
            "repeated": bool(repeated_kinds),
            "repeated_count": len(repeated_kinds),
            "kinds": repeated_kinds,
            # S2 §6: multiple fault rows inside ONE inspection are never
            # separate historical anomalies — count distinct inspections.
            "total_anomalies": len({f.inspection_id for f in anomaly_faults}),
            "message": message,
        }

    # ------------------------------------------------------------------
    # 7. Device health engine (§7)
    # ------------------------------------------------------------------
    def health(self, device: Device, exclude_demo: bool = True) -> dict:
        rows = self.thermal_rows(device.id, exclude_demo=exclude_demo)
        faults = self.device_faults(device.id)
        inspections = self.completed_inspections(device.id)
        anomaly_faults = [f for f, _ in faults if f.fault_type in _ANOMALY_KINDS]
        s = self.settings

        latest_classification = rows[-1].classification if rows and rows[-1].classification else None
        trend = self.temperature_trend(device.id, exclude_demo=exclude_demo)
        latest_fault = anomaly_faults[-1] if anomaly_faults else None
        repeated = self.repeated_anomalies(device)

        result = compute_health(
            HealthInputs(
                latest_classification=latest_classification,
                temperature_trend=None if trend.get("insufficient") else trend.get("status"),
                latest_fault_severity=latest_fault.severity if latest_fault else None,
                repeated_anomaly=repeated["repeated"],
                completed_inspections=len(inspections),
            ),
            weights={
                "thermal": s.health_weight_thermal,
                "trend": s.health_weight_trend,
                "anomaly": s.health_weight_anomaly,
                "repeated": s.health_weight_repeated,
                "history": s.health_weight_history,
            },
            min_inspections=s.health_min_inspections,
        )
        all_rows = self.thermal_rows(device.id, exclude_demo=False)
        simulated_count = sum(1 for r in all_rows if r.simulated)
        return {
            "score": result.score,
            "rating": result.rating,
            "contributors": [
                {"name": c.name, "rating": c.rating, "score": c.score, "weight": c.weight, "detail": c.detail}
                for c in result.contributors
            ],
            "insufficient": result.insufficient,
            "message": result.message,
            "disclaimer": result.disclaimer,
            "simulated_excluded": simulated_count if exclude_demo else 0,
            "temperatures_based_on_real_data": exclude_demo,
        }

    # ------------------------------------------------------------------
    # 8. Risk engine (§8)
    # ------------------------------------------------------------------
    def risk(self, device: Device, exclude_demo: bool = True) -> dict:
        rows = self.thermal_rows(device.id, exclude_demo=exclude_demo)
        faults = self.device_faults(device.id)
        anomaly_faults = [f for f, _ in faults if f.fault_type in _ANOMALY_KINDS]
        inspections = self.completed_inspections(device.id)
        trend = self.temperature_trend(device.id, exclude_demo=exclude_demo)
        repeated = self.repeated_anomalies(device)

        latest_row = rows[-1] if rows else None
        latest_fault = anomaly_faults[-1] if anomaly_faults else None
        s = self.settings

        result = compute_risk(
            RiskInputs(
                latest_classification=latest_row.classification if latest_row else None,
                latest_delta=latest_row.delta if latest_row else None,
                heat_path=latest_row.heat_path if latest_row else None,
                rapid_increase=bool(latest_row and latest_row.rapid_increase),
                temperature_trend=None if trend.get("insufficient") else trend.get("status"),
                repeated_anomaly=repeated["repeated"],
                latest_fault_severity=latest_fault.severity if latest_fault else None,
                has_any_inspection=bool(inspections),
                has_any_fault=bool(faults),
            ),
            rapid_bonus=s.risk_rapid_rise_bonus,
            repeat_bonus=s.risk_repeat_bonus,
        )
        return {
            "level": result.level,
            "risk_score": result.risk_score,
            "evidence": result.evidence,
            "reasoning": result.reasoning,
            "insufficient": result.insufficient,
            "message": result.message,
        }

    # ------------------------------------------------------------------
    # 9. Maintenance recommendations (§9)
    # ------------------------------------------------------------------
    def maintenance(self, device: Device) -> dict:
        risk = self.risk(device)
        trend = self.temperature_trend(device.id)
        repeated = self.repeated_anomalies(device)
        rec = maintenance_recommendation(
            risk_level=risk.get("level"),
            temperature_trend=None if trend.get("insufficient") else trend.get("status"),
            repeated_anomaly=repeated["repeated"],
        )
        open_orders = self.db.execute(
            select(MaintenanceRecord)
            .join(Inspection, MaintenanceRecord.inspection_id == Inspection.id)
            .where(Inspection.device_id == device.id, MaintenanceRecord.status.in_(("pending", "in_progress")))
            .order_by(MaintenanceRecord.created_at.desc())
            .limit(50)
        ).scalars().all()
        return {
            "recommendation": rec.recommendation,
            "basis": rec.basis,
            "risk_level": risk.get("level"),
            "open_orders": [
                {
                    "id": r.id,
                    "code": r.code,
                    "priority": r.priority,
                    "status": r.status,
                    "fault_type": r.fault_type,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in open_orders
            ],
            "insufficient": rec.insufficient,
        }

    # ------------------------------------------------------------------
    # 10. Inspection comparison (§10)
    # ------------------------------------------------------------------
    def comparison(self, device_id: int, exclude_demo: bool = True) -> dict:
        rows = self.thermal_rows(device_id, exclude_demo=exclude_demo)
        if len(rows) < 2:
            return {
                "current": None,
                "previous": None,
                "temperature_change": None,
                "delta_change": None,
                "trend": None,
                "insufficient": True,
                "message": "Insufficient historical data for comparison.",
            }
        current = rows[-1]
        previous = rows[-2]

        def _to_dict(row: ThermalHistory) -> dict:
            return {
                "inspection_id": row.inspection_id,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "temperature": round(row.max_temp, 2) if row.max_temp is not None else None,
                "thermal_delta": round(row.delta, 2) if row.delta is not None else None,
                "classification": row.classification,
                "simulated": row.simulated,
            }

        temperature_change = (current.max_temp - previous.max_temp) if (current.max_temp is not None and previous.max_temp is not None) else None
        delta_change = (current.delta - previous.delta) if (current.delta is not None and previous.delta is not None) else None
        if temperature_change is not None:
            if temperature_change > 2.0:
                trend = "INCREASING"
            elif temperature_change < -2.0:
                trend = "DECREASING"
            else:
                trend = "STABLE"
        else:
            trend = "UNKNOWN"
        return {
            "current": _to_dict(current),
            "previous": _to_dict(previous),
            "temperature_change": round(temperature_change, 2) if temperature_change is not None else None,
            "delta_change": round(delta_change, 2) if delta_change is not None else None,
            "trend": trend,
            "insufficient": False,
            "message": "Compared against the previous valid inspection.",
        }

    # ------------------------------------------------------------------
    # 11. Device timeline (§11) — real events only, never synthetic
    # ------------------------------------------------------------------
    def timeline(self, device: Device) -> dict:
        events: list[dict] = []
        if device.created_at:
            events.append(
                {
                    "date": device.created_at.isoformat(),
                    "type": "device_created",
                    "label": "Device registered",
                    "detail": f"{device.name} added to the system",
                }
            )

        for insp in self.completed_inspections(device.id):
            events.append(
                {
                    "date": (insp.ended_at or insp.started_at).isoformat(),
                    "type": "inspection_completed",
                    "label": "Inspection completed",
                    "detail": f"{insp.inspection_code} · risk score {insp.risk_score:.0f}",
                }
            )

        for fault, insp in self.device_faults(device.id):
            events.append(
                {
                    "date": insp.started_at.isoformat() if insp.started_at else None,
                    "type": "thermal_anomaly",
                    "label": "Thermal anomaly detected",
                    "detail": (fault.message or fault.fault_type)[:200],
                }
            )

        inspection_ids = [i.id for i in self.completed_inspections(device.id)]
        if inspection_ids:
            from backend.models.alarm import Alarm
            from backend.models.incident import Incident

            for alarm in self.db.scalars(select(Alarm).where(Alarm.inspection_id.in_(inspection_ids)).order_by(Alarm.created_at.asc())).all():
                events.append(
                    {
                        "date": alarm.created_at.isoformat() if alarm.created_at else None,
                        "type": "alarm",
                        "label": "Alarm raised",
                        "detail": (alarm.message or "")[:200],
                    }
                )
            for incident in self.db.scalars(
                select(Incident).where(Incident.inspection_id.in_(inspection_ids)).order_by(Incident.occurred_at.asc())
            ).all():
                events.append(
                    {
                        "date": incident.occurred_at.isoformat() if incident.occurred_at else None,
                        "type": "incident",
                        "label": "Incident report generated",
                        "detail": f"{incident.code} · {incident.fault_type}",
                    }
                )
            for record in self.db.scalars(
                select(MaintenanceRecord).where(MaintenanceRecord.inspection_id.in_(inspection_ids)).order_by(MaintenanceRecord.created_at.asc())
            ).all():
                events.append(
                    {
                        "date": record.created_at.isoformat() if record.created_at else None,
                        "type": "maintenance",
                        "label": "Maintenance work order generated",
                        "detail": f"{record.code} · {record.status}",
                    }
                )

        events.sort(key=lambda e: (e.get("date") or ""), reverse=True)
        return {"items": events}

    # ------------------------------------------------------------------
    # 12. Device status (§12)
    # ------------------------------------------------------------------
    def _status_from(self, device: Device, risk_level: str | None, repeated: bool, completed_count: int) -> str:
        if device.status_override:
            return device.derived_status
        if completed_count == 0:
            return ACTIVE
        if risk_level == "CRITICAL":
            return CRITICAL
        if risk_level == "ABNORMAL":
            return HIGH_RISK
        if risk_level == "ELEVATED" or repeated:
            return ATTENTION
        if risk_level == "NORMAL":
            return MONITORING
        return ACTIVE

    def refresh_derived_status(self, device: Device) -> str:
        """Recompute and persist the derived operational status (real data)."""
        if device.status_override:
            return device.derived_status
        completed = self.completed_inspections(device.id)
        risk_level = self.risk(device).get("level")
        repeated = self.repeated_anomalies(device)["repeated"]
        status = self._status_from(device, risk_level, repeated, len(completed))
        if status != device.derived_status:
            device.derived_status = status
            self.db.flush()
        return status

    # ------------------------------------------------------------------
    # 14. Dashboard aggregation (§14)
    # ------------------------------------------------------------------
    def dashboard(self, user: User) -> dict:
        org = user.organization
        device_ids = self.db.scalars(select(Device.id).where(organization_scope(org))).all()

        status_counts: dict[str, int] = {s: 0 for s in (ACTIVE, MONITORING, ATTENTION, HIGH_RISK, CRITICAL, MAINTENANCE, INACTIVE)}
        if device_ids:
            rows = self.db.execute(
                select(Device.derived_status, func.count()).where(Device.id.in_(device_ids)).group_by(Device.derived_status)
            ).all()
            for status, count in rows:
                status_counts[str(status)] = int(count)

        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        inspections_this_month = anomalies_this_month = 0
        avg_temperature = max_temperature = None
        if device_ids:
            inspections_this_month = int(
                self.db.scalar(
                    select(func.count())
                    .select_from(Inspection)
                    .where(Inspection.device_id.in_(device_ids), Inspection.started_at >= month_start)
                )
                or 0
            )
            anomalies_this_month = int(
                self.db.scalar(
                    select(func.count())
                    .select_from(Fault)
                    .join(Inspection, Fault.inspection_id == Inspection.id)
                    .where(Inspection.device_id.in_(device_ids), Inspection.started_at >= month_start)
                )
                or 0
            )
            avg_row = self.db.execute(
                select(func.avg(ThermalHistory.max_temp), func.max(ThermalHistory.max_temp)).where(
                    ThermalHistory.device_id.in_(device_ids), ThermalHistory.simulated.is_(False)
                )
            ).one()
            if avg_row[0] is not None:
                avg_temperature = round(float(avg_row[0]), 2)
                max_temperature = round(float(avg_row[1]), 2)

        # Devices requiring maintenance: manual MAINTENANCE status, high-risk
        # derived status, or an open maintenance work order.
        requiring_maintenance = set()
        if device_ids:
            requiring_maintenance.update(
                self.db.scalars(
                    select(Device.id).where(
                        Device.id.in_(device_ids),
                        Device.derived_status.in_((MAINTENANCE, HIGH_RISK, CRITICAL)),
                    )
                ).all()
            )
            requiring_maintenance.update(
                self.db.scalars(
                    select(Inspection.device_id)
                    .join(MaintenanceRecord, MaintenanceRecord.inspection_id == Inspection.id)
                    .where(Inspection.device_id.in_(device_ids), MaintenanceRecord.status.in_(("pending", "in_progress")))
                ).all()
            )

        return {
            "total_devices": len(device_ids),
            "healthy": status_counts.get(MONITORING, 0) + status_counts.get(ACTIVE, 0),
            "monitoring": status_counts.get(MONITORING, 0),
            "attention": status_counts.get(ATTENTION, 0),
            "high_risk": status_counts.get(HIGH_RISK, 0),
            "critical": status_counts.get(CRITICAL, 0),
            "maintenance": status_counts.get(MAINTENANCE, 0),
            "inactive": status_counts.get(INACTIVE, 0),
            "inspections_this_month": inspections_this_month,
            "anomalies_this_month": anomalies_this_month,
            "devices_requiring_maintenance": len(requiring_maintenance),
            "avg_temperature": avg_temperature,
            "max_temperature": max_temperature,
        }
