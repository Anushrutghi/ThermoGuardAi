"""S5 — thermal hardware abstraction contract tests (mock hardware only).

Proves the INTERFACE behavior a real sensor driver must satisfy, without any
physical device: connect/disconnect/start/stop, lifecycle transitions,
MEASURED/SIMULATED/UNAVAILABLE/INVALID measurement classification, and the
honesty rule that a sensor is never reported READY unless it actually
initialised. No hardware was connected for these tests — they validate the
architecture, not a device.
"""
from __future__ import annotations

import numpy as np

from ai.thermal.base import (
    BaseThermalSource,
    SensorMetadata,
    ThermalDataQuality,
    ThermalFrame,
    ThermalLifecycle,
    ThermalSensor,
)
from ai.thermal.factory import thermal_measurement_status, thermal_source_status
from ai.thermal.simulator import ThermalSimulator


class MockSensor(ThermalSensor):
    """A controllable fake sensor: available can be flipped, frames may be invalid."""

    name = "mock-sensor"

    def __init__(self, available: bool = True, simulated: bool = False) -> None:
        self._available = available
        self.simulated = simulated
        self.lifecycle = ThermalLifecycle.DISCONNECTED
        self.data_quality = ThermalDataQuality.VALID
        self._reads = 0

    @property
    def available(self) -> bool:
        return self._available

    def read(self) -> ThermalFrame | None:
        if not self._available:
            return None
        self._reads += 1
        return ThermalFrame(temperatures=np.full((4, 4), 25.0, dtype=np.float32), source=self.name)

    def get_metadata(self) -> SensorMetadata:
        return SensorMetadata(manufacturer="Mock", model="Mock Sensor", simulated=self.simulated)

    @property
    def emissivity(self) -> float | None:
        return 0.95 if self._available else None


# ---------------------------------------------------------------------------
# Lifecycle contract
# ---------------------------------------------------------------------------
def test_disconnected_by_default() -> None:
    src = MockSensor(available=True)
    assert src.lifecycle == ThermalLifecycle.DISCONNECTED
    assert not src.is_connected()


def test_connect_sets_connected_when_available() -> None:
    src = MockSensor(available=True)
    assert src.connect() is True
    assert src.is_connected()
    assert src.lifecycle == ThermalLifecycle.CONNECTED


def test_connect_fails_honestly_when_unavailable() -> None:
    src = MockSensor(available=False)
    assert src.connect() is False
    assert src.lifecycle == ThermalLifecycle.ERROR
    assert not src.is_connected(), "an unavailable sensor must never report connected"


def test_start_transitions_to_streaming() -> None:
    src = MockSensor(available=True)
    assert src.start() is True
    assert src.lifecycle == ThermalLifecycle.STREAMING
    assert src.is_connected()


def test_start_from_unavailable_is_honest() -> None:
    src = MockSensor(available=False)
    assert src.start() is False
    assert src.lifecycle == ThermalLifecycle.ERROR


def test_stop_disconnects() -> None:
    src = MockSensor(available=True)
    src.connect()
    src.stop()
    assert src.lifecycle == ThermalLifecycle.DISCONNECTED
    assert not src.is_connected()


def test_stop_is_idempotent() -> None:
    src = MockSensor(available=True)
    src.stop()
    src.stop()
    assert src.lifecycle == ThermalLifecycle.DISCONNECTED


def test_get_frame_validates_and_flags_quality() -> None:
    src = MockSensor(available=True)
    frame = src.get_frame()
    assert frame is not None
    assert frame.quality == ThermalDataQuality.VALID


def test_no_frame_marks_sensor_disconnected() -> None:
    """A sensor that stops returning frames must surface as disconnected/invalid."""
    src = MockSensor(available=False)
    frame = src.get_frame()
    assert frame is None
    assert src.data_quality == ThermalDataQuality.INVALID
    assert src.lifecycle == ThermalLifecycle.DISCONNECTED


# ---------------------------------------------------------------------------
# Measurement status classification (S5)
# ---------------------------------------------------------------------------
def test_measurement_simulated_for_demo() -> None:
    sim = ThermalSimulator()
    assert thermal_measurement_status(sim) == "SIMULATED"


def test_measurement_measured_for_real_connected() -> None:
    src = MockSensor(available=True, simulated=False)
    src.connect()
    src.get_frame()  # valid frame
    assert thermal_measurement_status(src) == "MEASURED"


def test_measurement_unavailable_when_not_connected() -> None:
    src = MockSensor(available=False, simulated=False)
    assert thermal_measurement_status(src) == "UNAVAILABLE"


def test_measurement_invalid_when_frame_failed_validation() -> None:
    src = MockSensor(available=True, simulated=False)
    src.connect()
    src.data_quality = ThermalDataQuality.INVALID
    assert thermal_measurement_status(src) == "INVALID"


# ---------------------------------------------------------------------------
# Authoritative status payload (backend-side REAL vs DEMO decision)
# ---------------------------------------------------------------------------
def test_status_hardware_demo_for_simulator() -> None:
    sim = ThermalSimulator()
    status = thermal_source_status(sim)
    assert status["hardware"] == "DEMO / SIMULATED THERMAL"
    assert status["measurement"] == "SIMULATED"
    assert status["simulated"] is True


def test_status_hardware_real_for_connected_mock() -> None:
    src = MockSensor(available=True, simulated=False)
    src.connect()
    status = thermal_source_status(src)
    assert status["hardware"] == "REAL SENSOR"
    assert status["measurement"] == "MEASURED"
    assert status["connected"] is True
    assert status["metadata"]["model"] == "Mock Sensor"


def test_status_never_ready_without_init() -> None:
    """A sensor that failed to initialise must never be reported READY/connected."""
    src = MockSensor(available=False)
    status = thermal_source_status(src)
    assert status["connected"] is False
    assert status["lifecycle"] in ("ERROR", "DISCONNECTED")
    assert status["measurement"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# Start/stop surface on every real driver + the simulator (interface parity)
# ---------------------------------------------------------------------------
def test_all_drivers_expose_lifecycle_contract() -> None:
    from ai.thermal.drivers import AMG8833Driver, LeptonDriver, MLX90640Driver, SeekThermalDriver

    for cls in (MLX90640Driver, LeptonDriver, AMG8833Driver, SeekThermalDriver):
        driver = cls()
        # interface methods exist (callable), regardless of hardware presence
        assert callable(driver.connect)
        assert callable(driver.disconnect)
        assert callable(driver.start)
        assert callable(driver.stop)
        assert callable(driver.read)
        assert callable(driver.get_metadata)
        # without hardware, drivers must not fake readiness
        if not driver.available:
            assert driver.start() is False or driver.lifecycle in (ThermalLifecycle.DISCONNECTED, ThermalLifecycle.ERROR)


def test_base_source_is_abstract_for_read() -> None:
    """The production interface requires a read() implementation (no silent stubs)."""
    import inspect

    assert inspect.isabstract(BaseThermalSource)
    assert "read" in BaseThermalSource.__abstractmethods__
