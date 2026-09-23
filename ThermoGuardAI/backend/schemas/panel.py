"""Panel and component schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from backend.schemas.common import ORMModel


class ComponentOut(ORMModel):
    id: int
    panel_id: int
    label: str
    component_type: str
    x: float
    y: float
    w: float
    h: float
    created_at: datetime


class PanelOut(ORMModel):
    id: int
    name: str
    code: str
    location: str | None = None
    description: str | None = None
    organization: str | None = None
    created_at: datetime


class PanelDetail(PanelOut):
    components: list[ComponentOut] = []


class PanelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=64)
    location: str | None = None
    description: str | None = None


class ComponentCreate(BaseModel):
    label: str
    component_type: str
    x: float = 0.0
    y: float = 0.0
    w: float = 0.1
    h: float = 0.1
