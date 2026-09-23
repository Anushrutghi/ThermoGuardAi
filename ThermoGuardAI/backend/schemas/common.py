"""Common/shared Pydantic schemas."""
from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):  # noqa: UP046 — py3.11-compatible generic for portability
    items: list[T]
    total: int
    offset: int = 0
    limit: int = 100


class Message(BaseModel):
    message: str
    details: dict[str, Any] | None = None


class HealthStatus(BaseModel):
    status: str
    app: str
    version: str
    database: str
    detector: str
    thermal: str
