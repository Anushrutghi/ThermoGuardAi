"""Temperature severity thresholds per component type.

Values are rise-above-ambient in °C, based on common thermographic
inspection guidance (IEEE/NETA style):
  - < 10 °C rise : healthy
  - 10–20 °C    : warning
  - 20–30 °C    : high risk
  - > 30 °C     : critical
Components like cables may have tighter limits.
"""
from __future__ import annotations

from ai.detector.base import HealthStatus

# (warning, high, critical) — delta above ambient in °C
THRESHOLDS: dict[str, tuple[float, float, float]] = {
    "circuit_breaker": (10.0, 20.0, 30.0),
    "fuse": (10.0, 20.0, 30.0),
    "relay": (12.0, 22.0, 32.0),
    "contactor": (12.0, 22.0, 32.0),
    "busbar": (10.0, 20.0, 30.0),
    "terminal": (8.0, 15.0, 25.0),  # connections are the most common failure point
    "cable": (8.0, 15.0, 25.0),
    "mccb": (10.0, 20.0, 30.0),
    "mcb": (10.0, 20.0, 30.0),
    "rccb": (10.0, 20.0, 30.0),
    "transformer": (15.0, 30.0, 45.0),  # transformers run warmer
    "motor_starter": (12.0, 25.0, 35.0),
    "disconnect_switch": (10.0, 20.0, 30.0),
    "power_supply": (15.0, 30.0, 40.0),
    "indicator_light": (10.0, 20.0, 30.0),
    "panel_door": (15.0, 30.0, 45.0),
    "warning_label": (15.0, 30.0, 45.0),
}

DEFAULT_THRESHOLDS: tuple[float, float, float] = (10.0, 20.0, 30.0)


def classify_delta(delta_c: float, component_type: str = "circuit_breaker") -> HealthStatus:
    """Classify a temperature rise above ambient into a severity level."""
    warning, high, critical = THRESHOLDS.get(component_type, DEFAULT_THRESHOLDS)
    if delta_c >= critical:
        return HealthStatus.CRITICAL
    if delta_c >= high:
        return HealthStatus.HIGH_RISK
    if delta_c >= warning:
        return HealthStatus.WARNING
    return HealthStatus.HEALTHY


def classify_absolute(temp_c: float, ambient: float, component_type: str = "circuit_breaker") -> HealthStatus:
    """Classify based on absolute temperature and ambient."""
    return classify_delta(temp_c - ambient, component_type)


def threshold_for(component_type: str) -> tuple[float, float, float]:
    return THRESHOLDS.get(component_type, DEFAULT_THRESHOLDS)
