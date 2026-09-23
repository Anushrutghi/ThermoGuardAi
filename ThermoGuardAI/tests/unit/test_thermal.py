"""Unit tests for thermal simulation and processing."""
from __future__ import annotations

import numpy as np

from ai.thermal.base import ThermalFrame
from ai.thermal.processing import compute_stats, overlay_on_rgb, region_temp, resize_to_rgb
from ai.thermal.simulator import ThermalSimulator


def test_simulator_returns_valid_frame() -> None:
    sim = ThermalSimulator(ambient=25.0, hot_component=True)
    frame = sim.read()
    assert isinstance(frame, ThermalFrame)
    assert frame.temperatures.shape == (64, 64)
    assert frame.min_temp >= 20.0
    # a strong hotspot must exist
    assert frame.max_temp > 55.0


def test_compute_stats_detects_hotspot() -> None:
    sim = ThermalSimulator(ambient=25.0)
    frame = sim.read()
    stats = compute_stats(frame)
    assert stats.max_temp > stats.min_temp
    assert 0.0 <= stats.hotspot_x <= 1.0
    assert 0.0 <= stats.hotspot_y <= 1.0
    assert stats.delta > 0
    assert stats.heat_spread >= 0.0


def test_region_temp_in_bbox() -> None:
    temps = np.full((10, 10), 25.0, dtype=np.float32)
    temps[2:5, 2:5] = 80.0
    frame = ThermalFrame(temperatures=temps)
    hot = region_temp(frame, (0.2, 0.2, 0.3, 0.3))
    cold = region_temp(frame, (0.7, 0.7, 0.2, 0.2))
    assert hot is not None and hot > 60.0
    assert cold is not None and cold < 30.0


def test_resize_and_overlay() -> None:
    sim = ThermalSimulator(ambient=25.0)
    frame = sim.read()
    rgb = np.zeros((100, 160, 3), dtype=np.uint8)
    resized = resize_to_rgb(frame, (100, 160))
    assert resized.temperatures.shape == (100, 160)
    overlay = overlay_on_rgb(rgb, resized.temperatures.astype(np.uint8))
    assert overlay.shape == rgb.shape
