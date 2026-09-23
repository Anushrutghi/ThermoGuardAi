"""Repository for the Component model."""
from __future__ import annotations

from sqlalchemy import func, select

from backend.models.component import Component
from backend.models.temperature_history import TemperatureReading
from backend.repositories.base import BaseRepository

# Stable display-code prefixes (spec: B1, B2, R1…)
_CODE_PREFIX: dict[str, str] = {
    "circuit_breaker": "B",
    "breaker": "B",
    "mcb": "B",
    "mccb": "B",
    "rccb": "B",
    "relay": "R",
    "contactor": "K",
    "fuse": "F",
    "busbar": "BB",
    "terminal": "T",
    "cable": "C",
    "transformer": "TR",
    "motor_starter": "MS",
    "disconnect_switch": "DS",
    "power_supply": "PS",
    "indicator_light": "IL",
    "panel_door": "PD",
    "warning_label": "WL",
}


def component_code_prefix(component_type: str) -> str:
    return _CODE_PREFIX.get(component_type, "X")


class ComponentRepository(BaseRepository[Component]):
    model = Component

    def list_for_panel(self, panel_id: int) -> list[Component]:
        stmt = (
            select(Component)
            .where(Component.panel_id == panel_id)
            .order_by(Component.label)
        )
        return list(self.db.scalars(stmt).all())

    def upsert_by_label(self, panel_id: int, label: str, component_type: str) -> Component:
        """Get or create a component by label — keeps digital map IDs stable.

        New components receive a stable display code (e.g. B3 for the third
        breaker on the panel) so history references stay readable.
        """
        existing = self.db.scalar(
            select(Component).where(Component.panel_id == panel_id, Component.label == label)
        )
        if existing:
            return existing
        seq = int(
            self.db.scalar(
                select(func.count())
                .select_from(Component)
                .where(Component.panel_id == panel_id, Component.component_type == component_type)
            )
            or 0
        )
        component = self.create(
            panel_id=panel_id,
            label=label,
            component_type=component_type,
            code=f"{component_code_prefix(component_type)}{seq + 1}",
        )
        return component

    def temperature_history(self, component_id: int, limit: int = 200) -> list[TemperatureReading]:
        stmt = (
            select(TemperatureReading)
            .where(TemperatureReading.component_id == component_id)
            .order_by(TemperatureReading.reading_time.desc())
            .limit(limit)
        )
        return list(reversed(list(self.db.scalars(stmt).all())))

    def temperature_histories(self, component_ids: list[int], limit: int = 200) -> dict[int, list[TemperatureReading]]:
        """Batch temperature history for many components (one query)."""
        if not component_ids:
            return {}
        rows = self.db.scalars(
            select(TemperatureReading)
            .where(TemperatureReading.component_id.in_(component_ids))
            .order_by(TemperatureReading.reading_time.asc())
        ).all()
        out: dict[int, list[TemperatureReading]] = {}
        for reading in rows:
            if reading.component_id is not None:
                bucket = out.setdefault(reading.component_id, [])
                if len(bucket) < limit:
                    bucket.append(reading)
        return out
