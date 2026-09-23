"""Generic repository base implementing the Repository pattern."""
from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.base import Base

T = TypeVar("T", bound=Base)


class BaseRepository[T]:  # noqa: UP046 — py3.11-compatible generic for portability
    """CRUD operations for a single ORM model."""

    model: type[T]

    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, id: int) -> T | None:
        return self.db.get(self.model, id)

    def get_or_raise(self, id: int) -> T:
        obj = self.get(id)
        if obj is None:
            raise LookupError(f"{self.model.__name__} {id} not found")
        return obj

    def list(self, limit: int = 100, offset: int = 0, **filters: Any) -> list[T]:
        stmt = select(self.model)
        for key, value in filters.items():
            if value is not None:
                stmt = stmt.where(getattr(self.model, key) == value)
        stmt = stmt.limit(limit).offset(offset)
        return list(self.db.scalars(stmt).all())

    def count(self, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model)
        for key, value in filters.items():
            if value is not None:
                stmt = stmt.where(getattr(self.model, key) == value)
        return int(self.db.scalar(stmt) or 0)

    def create(self, **values: Any) -> T:
        obj = self.model(**values)
        self.db.add(obj)
        self.db.flush()
        return obj

    def update(self, obj: T, **values: Any) -> T:
        for key, value in values.items():
            setattr(obj, key, value)
        self.db.flush()
        return obj

    def delete(self, obj: T) -> None:
        self.db.delete(obj)
        self.db.flush()
