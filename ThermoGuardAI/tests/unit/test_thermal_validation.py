"""S4 unit tests — thermal frame validation, lifecycle, metadata honesty.

Validates that NaN/inf/out-of-range temperatures can never enter analytics,
that the sensor lifecycle states exist, and that DEMO metadata never claims
real hardware capabilities (no invented emissivity).
"""
from __future__ import annotations

import numpy as np

from ai.thermal.base import (
    SensorMetadata,
    ThermalDataQuality,
    ThermalFrame,
    ThermalLifecycle,
    validate_thermal_frame,
)
from ai.thermal.factory import thermal_source_status
from ai.thermal.simulator import ThermalSimulator


# ---------------------------------------------------------------------------
# Frame validation
# ---------------------------------------------------------------------------
def _valid_frame() -> np.ndarray:
    rng = np.random.default_rng(7)
    return (25.0 + rng.normal(0, 0.5, (24, 32))).astype(np.float32)


def test_valid_frame_is_valid() -> None:
    result = validate_thermal_frame(_valid_frame())
    assert result.quality == ThermalDataQuality.VALID
    assert result.valid
    assert result.reasons == []
    assert result.shape == (24, 32)


def test_nan_values_rejected() -> None:
    temps = _valid_frame()
    temps[3, 5] = np.nan
    result = validate_thermal_frame(temps)
    assert result.quality == ThermalDataQuality.LOW_CONFIDENCE
    assert result.valid  # a single NaN is low-confidence, not fatal


def test_too_many_nan_values_invalid() -> None:
    temps = _valid_frame()
    temps[0:8, :] = np.nan  # 25% missing
    result = validate_thermal_frame(temps)
    assert result.quality == ThermalDataQuality.INVALID
    assert not result.valid
    assert any("missing" in r for r in result.reasons)


def test_infinity_values_rejected() -> None:
    temps = _valid_frame()
    temps[0, 0] = np.inf
    result = validate_thermal_frame(temps)
    assert result.quality == ThermalDataQuality.LOW_CONFIDENCE


def test_out_of_range_temperatures_invalid() -> None:
    temps = _valid_frame()
    temps[1, 1] = 999.0  # beyond any thermographic sensor range
    result = validate_thermal_frame(temps)
    assert result.quality == ThermalDataQuality.INVALID, result.reasons
    assert any("thermographic range" in r for r in result.reasons)

    cold = _valid_frame()
    cold[1, 1] = -500.0
    assert validate_thermal_frame(cold).quality == ThermalDataQuality.INVALID


def test_bad_dimensions_invalid() -> None:
    assert validate_thermal_frame(np.zeros((1, 4))).quality == ThermalDataQuality.INVALID
    assert validate_thermal_frame(np.zeros((4,))).quality == ThermalDataQuality.INVALID
    assert validate_thermal_frame("nope").quality == ThermalDataQuality.INVALID  # type: ignore[arg-type]
    # non-numeric dtypes (complex/bool/object) are INVALID — never coerced
    assert validate_thermal_frame(np.zeros((4, 4), dtype=np.complex64)).quality == ThermalDataQuality.INVALID
    assert validate_thermal_frame(np.zeros((4, 4), dtype=bool)).quality == ThermalDataQuality.INVALID


def test_thermal_frame_carries_quality() -> None:
    temps = _valid_frame()
    frame = ThermalFrame(temperatures=temps, source="test")
    assert frame.quality == ThermalDataQuality.VALID
    # properties survive invalid pixels (nan-aware)
    temps[0, 0] = np.nan
    assert frame.max_temp is not None


# ---------------------------------------------------------------------------
# Lifecycle states
# ---------------------------------------------------------------------------
def test_lifecycle_states_exist() -> None:
    states = {s.value for s in ThermalLifecycle}
    for expected in (
        "CONNECTING",
        "CONNECTED",
        "CALIBRATING",
        "STABILIZING",
        "BASELINE",
        "READY",
        "SCANNING",
        "DISCONNECTED",
        "ERROR",
    ):
        assert expected in states, expected


def test_quality_states_exist() -> None:
    values = {q.value for q in ThermalDataQuality}
    assert {"VALID", "LOW_CONFIDENCE", "INVALID"} <= values


# ---------------------------------------------------------------------------
# Simulator honesty: DEMO metadata, no invented emissivity
# ---------------------------------------------------------------------------
def test_simulator_metadata_is_honest() -> None:
    sim = ThermalSimulator()
    metadata = sim.get_metadata()
    assert metadata.simulated is True
    assert metadata.emissivity is None, "emissivity must never be invented"
    assert metadata.emissivity_supported is False
    assert "simulator" in metadata.model.lower()


def test_simulator_emissivity_unavailable() -> None:
    sim = ThermalSimulator()
    assert sim.emissivity is None
    assert sim.get_ambient_temperature() is not None
    assert sim.is_connected()


def test_simulator_get_frame_roundtrip() -> None:
    sim = ThermalSimulator()
    frame = sim.get_frame()
    assert frame is not None
    assert frame.quality in (ThermalDataQuality.VALID, ThermalDataQuality.LOW_CONFIDENCE)
    assert frame.temperatures.shape == (64, 64)
    tmap = sim.get_temperature_map()
    assert tmap is not None and tmap.ndim == 2


def test_thermal_source_status_authoritative() -> None:
    sim = ThermalSimulator()
    status = thermal_source_status(sim)
    assert status["simulated"] is True
    assert status["hardware"] == "DEMO / SIMULATED THERMAL"
    assert status["connected"] is True
    assert status["lifecycle"] in {s.value for s in ThermalLifecycle}
    assert status["quality"] in {q.value for q in ThermalDataQuality}
    assert status["metadata"]["emissivity"] is None
    assert status["disclaimer"]


def test_metadata_never_leaks_real_fields_for_demo() -> None:
    metadata = SensorMetadata(simulated=True)
    d = metadata.to_dict()
    assert d["serial_number"] is None
    assert d["firmware"] is None
    assert d["emissivity"] is None
