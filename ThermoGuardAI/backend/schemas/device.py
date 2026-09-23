"""Device schemas (S1 + S2 device intelligence)."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from backend.models.device import OPERATIONAL_STATUSES
from backend.schemas.common import ORMModel

# Legacy statuses remain accepted for backward compatibility (S1 UIs).
_LEGACY_STATUS = "^(active|attention|critical|inactive)$"


class DeviceBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    location: str | None = Field(default=None, max_length=255)
    device_type: str = Field(default="wall_switch", max_length=64)
    # --- S2 device intelligence metadata ---
    building: str | None = Field(default=None, max_length=128)
    floor: str | None = Field(default=None, max_length=64)
    room: str | None = Field(default=None, max_length=128)
    manufacturer: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=128)
    serial_number: str | None = Field(default=None, max_length=128)
    rated_voltage: float | None = Field(default=None, ge=0)
    rated_current: float | None = Field(default=None, ge=0)
    installation_date: date | None = None
    last_maintenance_date: date | None = None
    next_inspection_date: date | None = None
    organization: str | None = Field(default=None, max_length=128)
    notes: str | None = None


class DeviceCreate(DeviceBase):
    pass


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    location: str | None = Field(default=None, max_length=255)
    device_type: str | None = Field(default=None, max_length=64)
    building: str | None = Field(default=None, max_length=128)
    floor: str | None = Field(default=None, max_length=64)
    room: str | None = Field(default=None, max_length=128)
    manufacturer: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=128)
    serial_number: str | None = Field(default=None, max_length=128)
    rated_voltage: float | None = Field(default=None, ge=0)
    rated_current: float | None = Field(default=None, ge=0)
    installation_date: date | None = None
    last_maintenance_date: date | None = None
    next_inspection_date: date | None = None
    notes: str | None = None
    status: str | None = Field(default=None, pattern=_LEGACY_STATUS)
    risk_level: str | None = Field(default=None, pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$")
    # S2 derived operational status. MAINTENANCE / INACTIVE are manual,
    # admin-only overrides; other values reset the override so the system
    # recomputes the status from data.
    derived_status: str | None = Field(
        default=None, pattern="^(" + "|".join(OPERATIONAL_STATUSES) + ")$"
    )


class DeviceOut(DeviceBase, ORMModel):
    id: int
    status: str
    risk_level: str
    derived_status: str = "ACTIVE"
    status_override: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # computed per-device stats (isolated — never cross-device)
    inspections_count: int = 0
    last_inspection_at: datetime | None = None
    last_risk_score: float | None = None
    last_max_temp: float | None = None
    last_thermal_simulated: bool | None = None


class DeviceDetail(DeviceOut):
    recent_inspections: list = []  # list[InspectionOut] (set by the route)
    history: dict | None = None  # S2 device history summary (set by the route)
