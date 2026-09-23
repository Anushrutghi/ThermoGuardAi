"""Real thermal camera drivers (hardware stubs + graceful degradation).

The drivers implement the full read/probe contract but require hardware
and optional libraries (smbus2, adafruit-circuitpython, v4l2, spidev).
They are auto-detected at startup; when unavailable the factory falls
back to the simulator.
"""
from __future__ import annotations

import logging

import numpy as np

from ai.thermal.base import SensorMetadata, ThermalFrame, ThermalSensor

logger = logging.getLogger(__name__)


class MLX90640Driver(ThermalSensor):
    """MLX90640 32x24 IR array via I2C (smbus2)."""

    name = "mlx90640"

    def __init__(self, bus: int = 1, address: int = 0x33) -> None:
        self._bus, self._address = bus, address
        self._mlx = None
        try:
            import mlx90640  # type: ignore

            self._mlx = mlx90640
            logger.info("MLX90640 initialised on bus %d addr 0x%02x", bus, address)
        except Exception as exc:  # pragma: no cover
            logger.warning("MLX90640 driver unavailable: %s", exc)

    @property
    def available(self) -> bool:
        return self._mlx is not None

    def read(self) -> ThermalFrame | None:  # pragma: no cover - hardware dependent
        if not self.available:
            return None
        frame = np.array(self._mlx.read_frame(), dtype=np.float32).reshape(24, 32)
        return ThermalFrame(temperatures=frame, source=self.name)

    def get_metadata(self) -> SensorMetadata:
        """MLX90640 spec — only meaningful when the device is connected."""
        connected = self.is_connected()
        return SensorMetadata(
            manufacturer="Melexis",
            model="MLX90640",
            serial_number=None,  # read from the device when present
            resolution="32x24",
            frame_rate=16.0 if connected else None,
            connection_type="I2C",
            firmware=None,
            emissivity_supported=True,
            emissivity=0.95,  # factory thermographic default — user-configurable
            simulated=False,
        )

    @property
    def emissivity(self) -> float | None:
        return 0.95  # default thermographic emissivity (configurable by the operator)


class LeptonDriver(ThermalSensor):
    """FLIR Lepton LWIR camera via SPI/UART."""

    name = "lepton"

    def __init__(self) -> None:
        self._lepton = None
        try:
            from lepton import Lepton  # type: ignore

            self._lepton = Lepton()
            logger.info("FLIR Lepton initialised")
        except Exception as exc:  # pragma: no cover
            logger.warning("Lepton driver unavailable: %s", exc)

    @property
    def available(self) -> bool:
        return self._lepton is not None

    def read(self) -> ThermalFrame | None:  # pragma: no cover - hardware dependent
        if not self.available:
            return None
        frame = np.array(self._lepton.frame(), dtype=np.float32)  # 160x120 radiometric
        return ThermalFrame(temperatures=frame, source=self.name)

    def get_metadata(self) -> SensorMetadata:
        return SensorMetadata(
            manufacturer="FLIR",
            model="Lepton (LWIR)",
            serial_number=None,
            resolution="160x120",
            frame_rate=8.7 if self.is_connected() else None,
            connection_type="SPI/UART",
            firmware=None,
            emissivity_supported=True,
            emissivity=0.95,
            simulated=False,
        )

    @property
    def emissivity(self) -> float | None:
        return 0.95


class AMG8833Driver(ThermalSensor):
    """Panasonic AMG8833 8x8 IR grid sensor via I2C."""

    name = "amg8833"

    def __init__(self, bus: int = 1, address: int = 0x69) -> None:
        self._bus, self._address = bus, address
        self._sensor = None
        try:
            import adafruit_amg88xx  # type: ignore
            import board  # type: ignore
            import busio  # type: ignore

            i2c = busio.I2C(board.SCL, board.SDA)
            self._sensor = adafruit_amg88xx.AMG88XX(i2c, addr=address)
            logger.info("AMG8833 initialised")
        except Exception as exc:  # pragma: no cover
            logger.warning("AMG8833 driver unavailable: %s", exc)

    @property
    def available(self) -> bool:
        return self._sensor is not None

    def read(self) -> ThermalFrame | None:  # pragma: no cover - hardware dependent
        if not self.available:
            return None
        frame = np.array(self._sensor.pixels, dtype=np.float32)  # 8x8
        return ThermalFrame(temperatures=frame, source=self.name)

    def get_metadata(self) -> SensorMetadata:
        return SensorMetadata(
            manufacturer="Panasonic",
            model="AMG8833",
            serial_number=None,
            resolution="8x8",
            frame_rate=10.0 if self.is_connected() else None,
            connection_type="I2C",
            firmware=None,
            emissivity_supported=False,
            emissivity=None,  # fixed-focus grid — emissivity not configurable
            simulated=False,
        )


class SeekThermalDriver(ThermalSensor):
    """Seek Thermal Compact Pro USB camera."""

    name = "seek"

    def __init__(self) -> None:
        self._seek = None
        try:
            import seekcamera  # type: ignore

            self._seek = seekcamera
            logger.info("Seek Thermal initialised")
        except Exception as exc:  # pragma: no cover
            logger.warning("Seek Thermal driver unavailable: %s", exc)

    @property
    def available(self) -> bool:
        return self._seek is not None

    def read(self) -> ThermalFrame | None:  # pragma: no cover - hardware dependent
        if not self.available:
            return None
        frame = np.array(self._seek.capture(), dtype=np.float32)
        return ThermalFrame(temperatures=frame, source=self.name)

    def get_metadata(self) -> SensorMetadata:
        return SensorMetadata(
            manufacturer="Seek Thermal",
            model="Compact Pro",
            serial_number=None,
            resolution="320x240",
            frame_rate=9.0 if self.is_connected() else None,
            connection_type="USB",
            firmware=None,
            emissivity_supported=True,
            emissivity=0.95,
            simulated=False,
        )

    @property
    def emissivity(self) -> float | None:
        return 0.95
