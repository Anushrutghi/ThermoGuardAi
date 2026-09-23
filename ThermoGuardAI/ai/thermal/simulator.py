"""Realistic thermal simulator.

Generates synthetic thermal maps that mimic real electrical panels:
a cooler background with several component-like warm regions and one
clearly overheated hotspot. Deterministic-enough for testing, with
temporal evolution so trends can be observed.
"""
from __future__ import annotations

import logging
import time

import cv2
import numpy as np

from ai.thermal.base import BaseThermalSource, SensorMetadata, ThermalFrame, ThermalLifecycle
from backend.core.config import get_settings

logger = logging.getLogger(__name__)

_GRID = 64  # simulated sensor resolution (e.g. MLX90640 is 32x24, we upscale)

# Honest DEMO metadata — the simulator is never presented as a real sensor.
_DEMO_METADATA = SensorMetadata(
    manufacturer="ThermoGuard Demo",
    model="DEMO Thermal Simulator",
    serial_number=None,
    resolution=f"{_GRID}x{_GRID}",
    frame_rate=10.0,
    connection_type="simulated",
    firmware="simulator-1.0",
    emissivity_supported=False,
    emissivity=None,  # unavailable — never invented
    simulated=True,
)


class ThermalSimulator(BaseThermalSource):
    """Synthetic thermal source used when no hardware is present."""

    name = "simulator"
    simulated = True  # readings are simulated — UIs must label them DEMO

    def __init__(self, ambient: float | None = None, hot_component: bool = True) -> None:
        settings = get_settings()
        self._ambient = ambient if ambient is not None else settings.thermal_ambient_c
        self._hot_component = hot_component
        self._t0 = time.time()
        self.lifecycle = ThermalLifecycle.READY

    @property
    def available(self) -> bool:
        return True

    def connect(self) -> bool:
        """DEMO source is always ready once constructed."""
        self.lifecycle = ThermalLifecycle.READY
        return True

    def disconnect(self) -> None:
        self.lifecycle = ThermalLifecycle.DISCONNECTED
        self.close()

    def get_metadata(self) -> SensorMetadata:
        return _DEMO_METADATA

    @property
    def emissivity(self) -> float | None:
        return None  # unavailable for the simulator — never invented

    def get_ambient_temperature(self) -> float | None:
        return self._ambient

    def read(self, hotspots: list[dict] | None = None, blend_default: bool = False) -> ThermalFrame:
        """Generate a synthetic thermal frame.

        `hotspots` — optional list of regions. Each entry is a dict with:
          * cx, cy  — normalized center of the component
          * delta   — peak temperature rise above ambient (°C)
          * bbox    — normalized (x1, y1, x2, y2) bounds of the detected circuit
          * spread  — heat-flow factor: >1.0 means the heat radiates outward
                      past the circuit's walls into the surrounding wires
                      (the overloaded circuit leaks heat; healthy ones don't).

        `blend_default` — when True (and hotspots given), the detection heat is
        blended on top of a FULL-SCENE thermal map (gradient, wire runs and
        component heat everywhere in the frame) so the whole camera view shows
        temperature — not just the middle where components are detected.

        When `hotspots` is None, falls back to the default panel-like layout.
        """
        # Ambient base with mild spatial noise
        rng = np.random.default_rng(int(time.time() * 10) % 2**31)
        temps = self._ambient + rng.normal(0, 0.35, (24, 32)).astype(np.float32)

        if hotspots:
            if blend_default:
                temps = self._full_scene(temps)
            for hs in hotspots:
                if isinstance(hs, dict):
                    cx, cy = hs["cx"], hs["cy"]
                    delta = hs["delta"]
                    bbox = hs.get("bbox")
                    spread = hs.get("spread", 1.0)
                    if bbox:
                        box_w = bbox[2] - bbox[0]  # normalized width
                        radius = max(0.05, box_w * 0.45 * spread)
                    else:
                        radius = max(0.05, hs.get("radius", 0.12) * spread)
                    # body heat of the component
                    temps = self._add_gaussian_blob(temps, cx, cy, radius, delta)
                    # heat flow: a broad weak halo when the circuit is radiating
                    # heat outward (overload) — reaches the walls/wires
                    if spread > 1.0:
                        temps = self._add_gaussian_blob(temps, cx, cy, radius * 1.9, delta * 0.15)
                else:
                    cx, cy, radius, delta = hs
                    temps = self._add_gaussian_blob(temps, cx, cy, radius, delta)
        else:
            # A few warm component blobs
            for cx, cy, radius, delta in ((0.5, 0.35, 0.16, 14.0), (0.3, 0.7, 0.12, 9.0), (0.75, 0.65, 0.1, 6.0)):
                temps = self._add_gaussian_blob(temps, cx, cy, radius, delta)

            # Strong hotspot (overheating breaker/cable)
            if self._hot_component:
                hot_cx, hot_cy = 0.68 + 0.02 * np.sin(time.time() / 9), 0.28
                temps = self._add_gaussian_blob(temps, hot_cx, hot_cy, 0.09, 55.0)

        # Smooth + upsample to a nicer grid
        temps = _upsample(_smooth(temps), _GRID, _GRID)
        return ThermalFrame(temperatures=temps, timestamp=time.time(), source=self.name)

    def _full_scene(self, temps: np.ndarray) -> np.ndarray:
        """Heat across the ENTIRE sensor — not just the detected components.

        A real thermal camera shows temperature everywhere: a mild vertical
        gradient, warm wire runs spanning the full width, and component blobs
        in every corner of the frame. Detection heat is blended on top of this
        so the admin sees the whole scene's thermal picture, not only a hot
        patch in the middle.
        """
        h, w = temps.shape
        yy, xx = np.mgrid[0:h, 0:w]  # noqa: F841
        temps = temps + (yy / h).astype(np.float32) * 3.0  # panel gradient
        for yc in (0.18, 0.42, 0.62, 0.85):  # wire runs across the full width
            band = 2.5 * np.exp(-(((yy / h) - yc) ** 2) / (2 * 0.045**2))
            temps = temps + band.astype(np.float32)
        for cx, cy, r, d in (
            (0.05, 0.05, 0.12, 4.0),
            (0.95, 0.05, 0.10, 3.5),
            (0.05, 0.95, 0.11, 4.0),
            (0.95, 0.95, 0.12, 4.5),
        ):
            temps = self._add_gaussian_blob(temps, cx, cy, r, d)
        return temps

    def _add_gaussian_blob(self, temps: np.ndarray, cx: float, cy: float, radius: float, delta: float) -> np.ndarray:
        h, w = temps.shape
        yy, xx = np.mgrid[0:h, 0:w]
        dist2 = ((xx / w - cx) ** 2 + (yy / h - cy) ** 2) / (radius**2)
        return temps + delta * np.exp(-dist2).astype(np.float32)


def _smooth(temps: np.ndarray, k: int = 3) -> np.ndarray:
    return cv2.GaussianBlur(temps, (k, k), 0).astype(np.float32)


def _upsample(temps: np.ndarray, h: int, w: int) -> np.ndarray:
    return cv2.resize(temps, (w, h), interpolation=cv2.INTER_CUBIC).astype(np.float32)
