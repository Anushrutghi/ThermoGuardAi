"""Fault classification types."""
from __future__ import annotations

from dataclasses import dataclass

from ai.detector.base import HealthStatus


@dataclass
class Fault:
    """A classified fault with severity and recommendation."""

    fault_type: str
    severity: HealthStatus
    confidence: float
    component_label: str | None = None
    temperature: float | None = None
    message: str = ""
    recommendation: str = ""

    @property
    def severity_value(self) -> int:
        order = {HealthStatus.HEALTHY: 0, HealthStatus.WARNING: 1, HealthStatus.HIGH_RISK: 2, HealthStatus.CRITICAL: 3}
        return order[self.severity]
