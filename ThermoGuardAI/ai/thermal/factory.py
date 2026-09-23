"""Thermal source factory — auto-detects hardware, falls back to simulator."""
from __future__ import annotations

import logging

from ai.thermal.base import BaseThermalSource, SensorMetadata, ThermalLifecycle
from backend.core.config import get_settings

logger = logging.getLogger(__name__)

_source: BaseThermalSource | None = None

# Canonical disclaimer — thermal readings depend on sensor accuracy, emissivity,
# distance, angle, ambient conditions, surface material, reflection and the
# environment. The system reports surface thermal patterns, never confirmed
# electrical faults.
THERMAL_DISCLAIMER = (
    "Thermal readings depend on sensor accuracy, emissivity, distance, angle, ambient "
    "conditions, surface material, reflection and the environment. Results indicate "
    "surface thermal patterns that may require professional inspection — they do not "
    "constitute a confirmed electrical fault diagnosis."
)


def thermal_measurement_status(src: BaseThermalSource) -> str:
    """Classify a measurement as MEASURED / SIMULATED / UNAVAILABLE / INVALID.

    Only MEASURED may be treated as a real sensor observation:
      * SIMULATED   — DEMO simulator (always labelled, never presented as real)
      * UNAVAILABLE — no sensor / not connected / no frame available
      * INVALID     — sensor present but its last frame failed validation
      * MEASURED    — real sensor, connected, valid data
    """
    if bool(getattr(src, "simulated", False)):
        return "SIMULATED"
    connected = bool(getattr(src, "is_connected", lambda: False)())
    if not connected:
        return "UNAVAILABLE"
    quality = getattr(src, "data_quality", None)
    if quality is not None and getattr(quality, "value", str(quality)) == "INVALID":
        return "INVALID"
    return "MEASURED"


def thermal_source_status(source: BaseThermalSource | None = None) -> dict:
    """Authoritative backend-side thermal status (REAL vs DEMO is decided here).

    The frontend must display exactly what this endpoint reports — it never
    infers the source from client state.
    """
    settings = get_settings()
    src = source or get_thermal_source()
    metadata: SensorMetadata = src.get_metadata() if hasattr(src, "get_metadata") else SensorMetadata()
    simulated = bool(getattr(src, "simulated", False))
    lifecycle = getattr(src, "lifecycle", ThermalLifecycle.DISCONNECTED)
    lifecycle_value = lifecycle.value if hasattr(lifecycle, "value") else str(lifecycle)
    quality = getattr(src, "data_quality", None)
    quality_value = quality.value if hasattr(quality, "value") else str(quality or "VALID")
    ambient = None
    if hasattr(src, "get_ambient_temperature"):
        try:
            ambient = src.get_ambient_temperature()
        except Exception:  # noqa: BLE001
            logger.exception("Ambient temperature read failed")
    return {
        "mode": settings.thermal_mode,
        "source": src.name,
        "simulated": simulated,
        "hardware": "DEMO / SIMULATED THERMAL" if simulated else "REAL SENSOR",
        "connected": bool(getattr(src, "is_connected", lambda: False)()),
        "lifecycle": lifecycle_value,
        "quality": quality_value,
        "measurement": thermal_measurement_status(src),
        "ambient_c": round(float(ambient), 2) if ambient is not None else None,
        "emissivity": getattr(src, "emissivity", None),
        "metadata": metadata.to_dict() if hasattr(metadata, "to_dict") else {},
        "disclaimer": THERMAL_DISCLAIMER,
    }


def get_thermal_source(mode: str | None = None) -> BaseThermalSource:
    """Return the active thermal source (singleton).

    mode: auto|simulator|mlx90640|lepton|amg8833|seek
    """
    global _source
    if _source is not None:
        return _source

    settings = get_settings()
    requested = mode or settings.thermal_mode

    candidates: list[BaseThermalSource] = []
    if requested == "simulator":
        from ai.thermal.simulator import ThermalSimulator

        candidates = [ThermalSimulator(hot_component=settings.thermal_sim_hot_component)]
    elif requested == "mlx90640":
        from ai.thermal.drivers import MLX90640Driver

        candidates = [MLX90640Driver()]
    elif requested == "lepton":
        from ai.thermal.drivers import LeptonDriver

        candidates = [LeptonDriver()]
    elif requested == "amg8833":
        from ai.thermal.drivers import AMG8833Driver

        candidates = [AMG8833Driver()]
    elif requested == "seek":
        from ai.thermal.drivers import SeekThermalDriver

        candidates = [SeekThermalDriver()]
    else:  # auto
        from ai.thermal.drivers import AMG8833Driver, LeptonDriver, MLX90640Driver, SeekThermalDriver
        from ai.thermal.simulator import ThermalSimulator

        candidates = [MLX90640Driver(), LeptonDriver(), AMG8833Driver(), SeekThermalDriver(), ThermalSimulator()]

    for candidate in candidates:
        if candidate.available:
            candidate.connect()
            _source = candidate
            logger.info("Thermal source selected: %s (simulated=%s)", candidate.name, candidate.simulated)
            return candidate

    # Simulator should always be available as last resort
    from ai.thermal.simulator import ThermalSimulator

    _source = ThermalSimulator(hot_component=settings.thermal_sim_hot_component)
    _source.connect()
    logger.info("No thermal hardware detected; using DEMO simulator.")
    return _source


def reset_thermal_source() -> None:
    """Close and clear the cached source (mainly for tests)."""
    global _source
    if _source is not None:
        _source.close()
        _source = None
