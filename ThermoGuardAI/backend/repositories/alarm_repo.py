"""Repository for the Alarm model."""
from __future__ import annotations

from sqlalchemy import select

from backend.models.alarm import Alarm
from backend.repositories.base import BaseRepository


class AlarmRepository(BaseRepository[Alarm]):
    model = Alarm

    def list_open(self, limit: int = 50, scope=None) -> list[Alarm]:  # noqa: ANN001
        stmt = (
            select(Alarm)
            .where(Alarm.acknowledged.is_(False))
            .order_by(Alarm.created_at.desc())
            .limit(limit)
        )
        if scope is not None:
            stmt = stmt.where(scope)
        return list(self.db.scalars(stmt).all())
