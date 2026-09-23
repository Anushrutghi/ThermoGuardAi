"""Component model — an identified electrical component with health tracking."""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.fault import Fault
    from backend.models.maintenance import MaintenanceRecord
    from backend.models.panel import Panel
    from backend.models.temperature_history import TemperatureReading


class Component(Base, TimestampMixin):
    """A component detected/registered on a panel (digital panel map entry)."""

    __tablename__ = "components"

    id: Mapped[int] = mapped_column(primary_key=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("panels.id", ondelete="CASCADE"), index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), index=True, nullable=False)  # e.g. "Breaker B2"
    component_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    code: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)  # stable display code, e.g. B1 / R2
    # Normalized ROI in the panel digital map (0..1)
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    w: Mapped[float] = mapped_column(Float, default=0.1)
    h: Mapped[float] = mapped_column(Float, default=0.1)
    expected_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Asset lifecycle fields (asset lifecycle management)
    installed_at: Mapped[date | None] = mapped_column(Date, nullable=True)  # installation / last replacement date
    expected_life_years: Mapped[float] = mapped_column(Float, default=15.0, nullable=False)
    replacement_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_replaced_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    retired: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    panel: Mapped[Panel] = relationship(back_populates="components")
    faults: Mapped[list[Fault]] = relationship(back_populates="component")
    maintenance: Mapped[list[MaintenanceRecord]] = relationship(back_populates="component")
    temperature_readings: Mapped[list[TemperatureReading]] = relationship(back_populates="component")

    @property
    def age_years(self) -> float | None:
        """Approximate asset age in years (from installed date or panel creation)."""
        from datetime import date as _date

        if self.installed_at:
            return round((_date.today() - self.installed_at).days / 365.25, 2)
        return None

    @property
    def life_used_pct(self) -> float | None:
        """Fraction of expected service life already consumed (0..100)."""
        age = self.age_years
        if age is None or not self.expected_life_years:
            return None
        return round(min(100.0, max(0.0, age / self.expected_life_years * 100.0)), 1)

    def __repr__(self) -> str:
        return f"<Component {self.label} ({self.component_type}) on panel {self.panel_id}>"
