"""Thermal source interfaces and data types (S4 production hardening).

Adds the production ``ThermalSensor`` contract on top of the original
``BaseThermalSource``: an explicit connect/disconnect lifecycle, authoritative
metadata, an ambient-temperature reading and per-frame data-quality reporting.
The backend/source is the single authority for REAL vs DEMO/SIMULATED — the
frontend must never infer it from client state.

Lifecycle (canonical S4 states):
    CONNECTING → CONNECTED → CALIBRATING → STABILIZING → BASELINE → READY
    → SCANNING → (…) → DISCONNECTED
Any failure anywhere → ERROR (retry is explicit, never silent).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np


class ThermalLifecycle(StrEnum):
    """Authoritative sensor lifecycle states (S4 + S5).

    S5 adds INITIALIZING / STREAMING / RECONNECTING so the canonical
    production model is: DISCONNECTED → CONNECTING → CONNECTED → INITIALIZING
    → READY → STREAMING → ERROR / RECONNECTING → (…) → DISCONNECTED.
    A sensor is never exposed as READY unless it actually passed
    initialization; the DEMO simulator is always labelled simulated.
    """

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    INITIALIZING = "INITIALIZING"
    CALIBRATING = "CALIBRATING"
    STABILIZING = "STABILIZING"
    BASELINE = "BASELINE"
    READY = "READY"
    SCANNING = "SCANNING"
    STREAMING = "STREAMING"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"


class ThermalDataQuality(StrEnum):
    """Quality of a thermal measurement (S4 §data quality).

    Analytics may only be produced from VALID or LOW_CONFIDENCE data; INVALID
    frames must never feed risk analysis.
    """

    VALID = "VALID"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    INVALID = "INVALID"


@dataclass
class SensorMetadata:
    """Metadata reported by a real sensor — only set when actually available.

    Every field defaults to None/unavailable; drivers fill in the values they
    genuinely know. ``emissivity=None`` means *unavailable* (never invented).
    """

    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    resolution: str | None = None
    frame_rate: float | None = None
    connection_type: str | None = None
    firmware: str | None = None
    temperature_unit: str = "celsius"
    # Emissivity: only when the sensor/environment actually provides it.
    emissivity: float | None = None
    emissivity_supported: bool = False
    simulated: bool = False

    def to_dict(self) -> dict:
        return {
            "manufacturer": self.manufacturer,
            "model": self.model,
            "serial_number": self.serial_number,
            "resolution": self.resolution,
            "frame_rate": self.frame_rate,
            "connection_type": self.connection_type,
            "firmware": self.firmware,
            "temperature_unit": self.temperature_unit,
            "emissivity": self.emissivity,
            "emissivity_supported": self.emissivity_supported,
            "simulated": self.simulated,
        }


@dataclass
class FrameValidation:
    """Result of validating one thermal frame (S4 §frame validation)."""

    quality: ThermalDataQuality = ThermalDataQuality.VALID
    reasons: list[str] = field(default_factory=list)
    shape: tuple[int, int] | None = None

    @property
    def valid(self) -> bool:
        return self.quality != ThermalDataQuality.INVALID

    def to_dict(self) -> dict:
        return {
            "quality": self.quality.value,
            "reasons": self.reasons,
            "shape": list(self.shape) if self.shape else None,
        }


@dataclass
class ThermalFrame:
    """A thermal frame: 2D temperature array in degrees Celsius."""

    temperatures: np.ndarray  # float32, shape (H, W)
    timestamp: float = 0.0
    source: str = "unknown"
    # S4: quality of this frame, set by validation before analytics use it.
    quality: ThermalDataQuality = ThermalDataQuality.VALID
    quality_reasons: list[str] = field(default_factory=list)

    @property
    def shape(self) -> tuple[int, int]:
        return self.temperatures.shape

    @property
    def max_temp(self) -> float:
        return float(np.nanmax(self.temperatures))

    @property
    def min_temp(self) -> float:
        return float(np.nanmin(self.temperatures))

    @property
    def mean_temp(self) -> float:
        return float(np.nanmean(self.temperatures))


class BaseThermalSource(ABC):
    """Interface all thermal camera drivers implement.

    S4 additions (with safe defaults so existing drivers keep working):

      * ``connect`` / ``disconnect`` / ``is_connected`` — explicit lifecycle.
      * ``get_frame`` / ``get_temperature_map`` / ``get_ambient_temperature`` —
        production read contract (``read()`` stays as the original hook).
      * ``get_metadata`` — authoritative sensor metadata (None = unavailable).
      * ``emissivity`` — only when the sensor supports it, else None.
      * ``lifecycle`` / ``data_quality`` — current authoritative state.
    """

    name: str = "base"
    # True for simulated sources. UIs must never present readings from a
    # simulated source as real measurements without a visible DEMO/SIMULATED
    # label.
    simulated: bool = False

    lifecycle: ThermalLifecycle = ThermalLifecycle.DISCONNECTED
    data_quality: ThermalDataQuality = ThermalDataQuality.VALID
    quality_reasons: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Original contract (drivers implement)
    # ------------------------------------------------------------------
    @abstractmethod
    def read(self) -> ThermalFrame | None:
        """Return the latest thermal frame or None."""
        raise NotImplementedError

    @property
    def available(self) -> bool:
        """Whether the hardware is present and initialised."""
        return False

    def close(self) -> None:
        """Release hardware resources."""

    # ------------------------------------------------------------------
    # S4 production lifecycle
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """Open the connection to the sensor. Returns True when connected."""
        self.lifecycle = ThermalLifecycle.CONNECTED if self.available else ThermalLifecycle.ERROR
        return self.is_connected()

    def disconnect(self) -> None:
        """Close the connection and release hardware resources."""
        self.lifecycle = ThermalLifecycle.DISCONNECTED
        self.close()

    # ------------------------------------------------------------------
    # S5 streaming lifecycle (start/stop aliases for simple drivers)
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Start streaming: connect if needed, then transition to STREAMING.

        Returns True when the sensor is actually streaming (connected and
        initialised). Drivers that genuinely need a separate streaming step
        may override this.
        """
        if not self.is_connected():
            if not self.connect():
                return False
        self.lifecycle = ThermalLifecycle.STREAMING
        return True

    def stop(self) -> None:
        """Stop streaming and release the connection (idempotent)."""
        self.disconnect()

    def is_connected(self) -> bool:
        """Whether the sensor is present and initialised."""
        return self.available and self.lifecycle != ThermalLifecycle.DISCONNECTED

    # ------------------------------------------------------------------
    # S4 production read contract
    # ------------------------------------------------------------------
    def get_frame(self) -> ThermalFrame | None:
        """Return the latest validated thermal frame or None when unavailable."""
        frame = self.read()
        if frame is None:
            self.lifecycle = ThermalLifecycle.DISCONNECTED
            self.data_quality = ThermalDataQuality.INVALID
            self.quality_reasons = ["No frame received from sensor"]
            return None
        validation = validate_thermal_frame(frame.temperatures)
        frame.quality = validation.quality
        frame.quality_reasons = validation.reasons
        self.data_quality = validation.quality
        self.quality_reasons = list(validation.reasons)
        return frame

    def get_temperature_map(self) -> np.ndarray | None:
        """Return the raw 2-D temperature map (degrees C) or None."""
        frame = self.get_frame()
        return frame.temperatures if frame is not None else None

    def get_ambient_temperature(self) -> float | None:
        """Ambient temperature reported by the sensor/environment, if any."""
        return None

    def get_metadata(self) -> SensorMetadata:
        """Authoritative sensor metadata (defaults are 'unavailable')."""
        return SensorMetadata(simulated=self.simulated)

    @property
    def emissivity(self) -> float | None:
        """Configured emissivity, or None when the sensor cannot provide it."""
        return None


def validate_thermal_frame(
    temperatures: np.ndarray,
    min_temp_c: float = -40.0,
    max_temp_c: float = 300.0,
    max_invalid_fraction: float = 0.05,
) -> FrameValidation:
    """Validate a raw thermal temperature array (S4 §frame validation).

    Checks, in order:
      1. It is a 2-D numeric array with at least 2x2 pixels.
      2. No NaN / positive-inf / negative-inf values leak into analytics.
      3. All values are inside the thermographic range [min_temp_c, max_temp_c].
      4. The fraction of missing/invalid pixels stays below the threshold.

    Never allows NaN/infinity/out-of-range values to enter analytics — an
    INVALID frame must be discarded (or flagged) by the caller.
    """
    reasons: list[str] = []
    shape: tuple[int, int] | None = None

    if not isinstance(temperatures, np.ndarray):
        reasons.append("temperature data is not an ndarray")
        return FrameValidation(ThermalDataQuality.INVALID, reasons)
    if not np.issubdtype(temperatures.dtype, np.number) or np.issubdtype(
        temperatures.dtype, np.complexfloating
    ):
        reasons.append(f"temperature data is not real-numeric (dtype={temperatures.dtype})")
        return FrameValidation(ThermalDataQuality.INVALID, reasons)
    if temperatures.ndim != 2 or temperatures.shape[0] < 2 or temperatures.shape[1] < 2:
        reasons.append(f"unexpected frame dimensions {temperatures.shape}")
        return FrameValidation(ThermalDataQuality.INVALID, reasons)
    shape = (temperatures.shape[0], temperatures.shape[1])

    arr = temperatures.astype(np.float32, copy=False)
    nonfinite = np.logical_not(np.isfinite(arr))
    nan_count = int(np.isnan(arr).sum())
    inf_count = int(np.isposinf(arr).sum() + np.isneginf(arr).sum())
    total = arr.size
    if nonfinite.any():
        reasons.append(f"frame contains {nan_count} NaN and {inf_count} Inf values")
    finite = arr[np.isfinite(arr)]
    if finite.size:
        below = float(finite.min()) < min_temp_c
        above = float(finite.max()) > max_temp_c
        if below or above:
            reasons.append(
                f"frame values outside thermographic range "
                f"[{min_temp_c}, {max_temp_c}] °C ({float(finite.min()):.1f}..{float(finite.max()):.1f})"
            )
    invalid_fraction = float(nonfinite.sum()) / float(total) if total else 1.0
    if invalid_fraction > max_invalid_fraction:
        reasons.append(f"{invalid_fraction:.1%} of pixels are missing/invalid (limit {max_invalid_fraction:.0%})")

    if reasons:
        quality = ThermalDataQuality.LOW_CONFIDENCE if not nonfinite.all() else ThermalDataQuality.INVALID
        # Out-of-range finite values are always INVALID — they cannot enter analytics.
        if any("outside thermographic range" in r for r in reasons):
            quality = ThermalDataQuality.INVALID
        if invalid_fraction > max_invalid_fraction:
            quality = ThermalDataQuality.INVALID
        return FrameValidation(quality, reasons, shape)
    return FrameValidation(ThermalDataQuality.VALID, [], shape)


class ThermalSensor(BaseThermalSource):
    """Marker base for real (non-simulated) production sensors.

    Real drivers should subclass this so callers can distinguish a hardware
    sensor from a DEMO simulator without guessing by name. The read contract
    is inherited from ``BaseThermalSource``.
    """

    simulated = False


class InvalidThermalFrameError(ValueError):
    """Raised when a caller insists on consuming an INVALID thermal frame."""
