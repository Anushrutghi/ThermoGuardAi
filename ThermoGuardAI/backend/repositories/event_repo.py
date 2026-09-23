"""Repository for the EventLog model."""
from __future__ import annotations

from backend.models.event import EventLog
from backend.repositories.base import BaseRepository


class EventLogRepository(BaseRepository[EventLog]):
    model = EventLog

    def log(self, level: str, source: str, message: str, user_id: int | None = None, details: dict | None = None) -> EventLog:
        return self.create(
            level=level,
            source=source,
            message=message,
            user_id=user_id,
            details_json=__import__("json").dumps(details) if details else None,
        )
