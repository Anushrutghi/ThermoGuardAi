"""Analytics aggregation service — summaries, trends, fault analysis."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.fault import Fault
from backend.models.inspection import Inspection
from backend.models.temperature_history import TemperatureReading

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Computes dashboard analytics from the database."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    def summary(self, days: int = 30) -> dict:
        since = datetime.now(UTC) - timedelta(days=days)

        total = int(self.db.scalar(select(func.count()).select_from(Inspection).where(Inspection.started_at >= since)) or 0)
        total_faults = int(self.db.scalar(select(func.count()).select_from(Fault).join(Inspection).where(Inspection.started_at >= since)) or 0)
        critical = int(
            self.db.scalar(
                select(func.count())
                .select_from(Fault)
                .join(Inspection)
                .where(Inspection.started_at >= since, Fault.severity == "critical")
            )
            or 0
        )
        avg_risk = float(self.db.scalar(select(func.avg(Inspection.risk_score)).where(Inspection.started_at >= since)) or 0.0)

        # top fault types
        top_rows = (
            self.db.execute(
                select(Fault.fault_type, func.count())
                .join(Inspection)
                .where(Inspection.started_at >= since)
                .group_by(Fault.fault_type)
                .order_by(func.count().desc())
                .limit(8)
            ).all()
        )
        top_faults = [{"fault_type": t, "count": int(c)} for t, c in top_rows]

        # severity breakdown
        sev_rows = (
            self.db.execute(
                select(Fault.severity, func.count()).join(Inspection).where(Inspection.started_at >= since).group_by(Fault.severity)
            ).all()
        )
        severity = {str(s): int(c) for s, c in sev_rows}
        for key in ("healthy", "warning", "high", "critical"):
            severity.setdefault(key, 0)

        return {
            "period_days": days,
            "total_inspections": total,
            "total_faults": total_faults,
            "critical_faults": critical,
            "avg_risk": round(avg_risk, 1),
            "top_fault_types": top_faults,
            "severity_breakdown": severity,
        }

    # ------------------------------------------------------------------
    def inspections_per_day(self, days: int = 30) -> list[dict]:
        since = datetime.now(UTC) - timedelta(days=days)
        rows = self.db.execute(
            select(func.date(Inspection.started_at), func.count())
            .where(Inspection.started_at >= since)
            .group_by(func.date(Inspection.started_at))
            .order_by(func.date(Inspection.started_at))
        ).all()
        return [{"date": str(day), "count": int(count)} for day, count in rows]

    def temperature_series(self, days: int = 30, limit: int = 300) -> list[dict]:
        """Recent temperature readings: {time, label, temp}."""
        since = datetime.now(UTC) - timedelta(days=days)
        rows = self.db.execute(
            select(TemperatureReading.reading_time, TemperatureReading.component_label, TemperatureReading.temperature)
            .where(TemperatureReading.reading_time >= since)
            .order_by(TemperatureReading.reading_time.desc())
            .limit(limit)
        ).all()
        return [{"time": t.isoformat(), "label": label, "temp": round(temp, 2)} for t, label, temp in rows]

    def component_health(self, limit: int = 100) -> list[dict]:
        """Latest + aggregated temperature stats per component label."""
        rows = self.db.execute(
            select(
                TemperatureReading.component_label,
                func.max(TemperatureReading.temperature),
                func.avg(TemperatureReading.temperature),
                func.count(TemperatureReading.id),
            )
            .group_by(TemperatureReading.component_label)
            .limit(limit)
        ).all()
        out = []
        for label, tmax, tavg, count in rows:
            health = "healthy"
            if tmax and tmax >= 60:
                health = "critical"
            elif tmax and tmax >= 50:
                health = "high"
            elif tmax and tmax >= 40:
                health = "warning"
            out.append(
                {
                    "label": label,
                    "max_temp": round(float(tmax), 1) if tmax is not None else None,
                    "avg_temp": round(float(tavg), 1) if tavg is not None else None,
                    "readings": int(count),
                    "health": health,
                }
            )
        return out
