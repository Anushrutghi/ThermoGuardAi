"""ThermalHistory model — one per-inspection thermal summary record (S2).

S1 stores the raw temperature time-series per inspection in
``temperature_history``. S2 adds a *per-inspection summary* row so device
analytics (trends, deltas, comparisons) can read one record per completed
switch inspection instead of thousands of samples. Values are written only
from a real ``ThermalScanResult`` — never fabricated.

DEMO/SIMULATED honesty: every row records the thermal source that produced it
and whether it was simulated. Real-world analytics must exclude ``simulated``
rows (the ``exclude_demo`` filter on the API does exactly that); the two are
never silently combined.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.device import Device
    from backend.models.inspection import Inspection


class ThermalHistory(Base, TimestampMixin):
    """Aggregated thermal measurements for one completed inspection."""

    __tablename__ = "thermal_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(
        ForeignKey("inspections.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True, nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    # temperatures (°C)
    max_temp: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_temp: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_temp: Mapped[float | None] = mapped_column(Float, nullable=True)
    reference_temp: Mapped[float | None] = mapped_column(Float, nullable=True)  # surrounding-wall reference
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)  # switch − reference (ΔT)
    # hotspot information (normalized 0..1 within the frame)
    hotspot_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    hotspot_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    # trend information from the scan
    trend_c_per_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend_delta_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    rapid_increase: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    heat_path: Mapped[str | None] = mapped_column(String(16), nullable=True)  # localized|extended|unknown
    # thermal mode + sensor information
    classification: Mapped[str | None] = mapped_column(String(16), nullable=True)  # NORMAL|ELEVATED|ABNORMAL|CRITICAL
    thermal_source: Mapped[str | None] = mapped_column(String(64), nullable=True)  # simulator|mlx90640|…
    # Emissivity of the sensor that ACTUALLY produced the measurement (None =
    # unavailable / never invented). Recorded from the active source, never the
    # global singleton when a session source exists (S4 final hardening).
    emissivity: Mapped[float | None] = mapped_column(Float, nullable=True)
    mode: Mapped[str | None] = mapped_column(String(32), nullable=True)  # inspection mode (switch_first…)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # DEMO flag — never mix

    inspection: Mapped[Inspection] = relationship(back_populates="thermal_history")
    device: Mapped[Device | None] = relationship(back_populates="thermal_history")

    def to_dict(self) -> dict:
        return {
            "inspection_id": self.inspection_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "max_temp": round(self.max_temp, 2) if self.max_temp is not None else None,
            "min_temp": round(self.min_temp, 2) if self.min_temp is not None else None,
            "avg_temp": round(self.avg_temp, 2) if self.avg_temp is not None else None,
            "reference_temp": round(self.reference_temp, 2) if self.reference_temp is not None else None,
            "delta": round(self.delta, 2) if self.delta is not None else None,
            "hotspot": {"x": round(self.hotspot_x, 3), "y": round(self.hotspot_y, 3)} if self.hotspot_x is not None else None,
            "trend_c_per_min": round(self.trend_c_per_min, 2) if self.trend_c_per_min is not None else None,
            "trend_delta_c": round(self.trend_delta_c, 2) if self.trend_delta_c is not None else None,
            "rapid_increase": self.rapid_increase,
            "heat_path": self.heat_path,
            "classification": self.classification,
            "thermal_mode": self.mode,
            "sensor": self.thermal_source,
            "emissivity": round(self.emissivity, 3) if self.emissivity is not None else None,
            "simulated": self.simulated,
        }
