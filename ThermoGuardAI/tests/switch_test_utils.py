"""Shared helpers for S1 switch-first tests (synthetic switch frames + fake thermal)."""
from __future__ import annotations

import time

import numpy as np

from ai.thermal.base import ThermalFrame


def make_switch_frame(
    h: int = 480,
    w: int = 640,
    cx: int | None = None,
    cy: int | None = None,
    plate_w: int = 120,
    plate_h: int = 160,
    brightness: int = 200,
    rocker: int = 90,
    wall: int = 120,
    noise_std: float = 2.0,
    seed: int = 7,
) -> np.ndarray:
    """A synthetic wall switch: light plate + darker central rocker on a wall."""
    cx = cx if cx is not None else w // 2
    cy = cy if cy is not None else h // 2
    frame = np.full((h, w, 3), wall, dtype=np.uint8)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, noise_std, (h, w)).astype(np.float32)
    for c in range(3):
        frame[:, :, c] = np.clip(frame[:, :, c].astype(np.float32) + noise, 0, 255).astype(np.uint8)
    x1, y1 = cx - plate_w // 2, cy - plate_h // 2
    x2, y2 = cx + plate_w // 2, cy + plate_h // 2
    frame[max(0, y1) : y2, max(0, x1) : x2] = brightness
    rw, rh = max(10, plate_w // 2), max(10, plate_h // 3)
    frame[cy - rh // 2 : cy + rh // 2, cx - rw // 2 : cx + rw // 2] = rocker
    return frame


def make_plain_wall(h: int = 480, w: int = 640, wall: int = 120, noise_std: float = 3.0, seed: int = 3) -> np.ndarray:
    """A textureless wall (no switch)."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, noise_std, (h, w, 1)).astype(np.float32)
    frame = np.clip(wall + noise, 0, 255).astype(np.uint8)
    return np.repeat(frame, 3, axis=2)


class RampedThermalSource:
    """Fake thermal source: a hot gaussian centred in the frame.

    `ramp` (float | None): °C added to the hotspot delta on every read, so a
    time-series can show a rising temperature trend.
    """

    name = "fake-thermal"
    simulated = True  # fake — tests never claim real measurements

    def __init__(self, ambient: float = 25.0, delta: float = 22.0, radius: float = 0.035, shape=(24, 32), ramp: float | None = None, seed: int = 11, emissivity: float | None = None) -> None:
        self.ambient = ambient
        self.base_delta = delta
        self.radius = radius
        self.shape = shape
        self.ramp = ramp
        # emissivity only when the fake sensor "supports" it (None = unavailable)
        self.emissivity = emissivity
        self._reads = 0
        self._rng = np.random.default_rng(seed)

    @property
    def available(self) -> bool:
        return True

    def read(self, hotspots: list | None = None, blend_default: bool = False) -> ThermalFrame:
        h, w = self.shape
        delta = self.base_delta + (self.ramp or 0.0) * self._reads
        self._reads += 1
        yy, xx = np.mgrid[0:h, 0:w]
        dist2 = ((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2) / (self.radius**2)
        temps = self.ambient + self._rng.normal(0, 0.2, (h, w))
        temps = temps + delta * np.exp(-dist2)
        return ThermalFrame(temperatures=temps.astype(np.float32), timestamp=float(self._reads), source=self.name)


class NoThermalSource:
    """A source that never provides frames (sensor unavailable path)."""

    name = "no-thermal"
    simulated = False

    @property
    def available(self) -> bool:
        return False

    def read(self, hotspots: list | None = None, blend_default: bool = False) -> None:
        return None


class FastSettings:
    """Small config values so controller tests run quickly."""

    switch_min_confidence = 0.45
    switch_stable_frames = 3
    switch_roi_padding = 0.35
    switch_size_min = 0.06
    switch_size_max = 0.55
    switch_center_tolerance = 0.35
    switch_min_brightness = 40.0
    switch_min_sharpness = 18.0
    thermal_baseline_frames = 2
    thermal_scan_frames = 3
    thermal_elevated_delta_c = 10.0
    thermal_abnormal_delta_c = 20.0
    thermal_critical_delta_c = 35.0
    thermal_rapid_rise_c_per_min = 6.0


def current_ms() -> float:
    return time.time()
