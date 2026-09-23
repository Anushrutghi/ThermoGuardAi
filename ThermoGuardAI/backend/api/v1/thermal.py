"""Thermal subsystem status routes (S4).

The backend/source is the single authority for whether thermal readings come
from a REAL SENSOR or DEMO/SIMULATED THERMAL. The frontend must render exactly
what this endpoint reports and must never infer the source from client state.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from ai.thermal.factory import get_thermal_source, thermal_source_status
from backend.api.deps import get_current_user
from backend.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/thermal", tags=["thermal"])


@router.get("/status")
def thermal_status(_: User = Depends(get_current_user)) -> dict:  # noqa: B008
    """Authoritative thermal subsystem status: source, lifecycle, quality, metadata.

    ``hardware`` is exactly ``REAL SENSOR`` or ``DEMO / SIMULATED THERMAL`` —
    decided here, never in the frontend. ``metadata`` fields are None whenever
    the sensor cannot provide them (serial numbers, firmware, emissivity…).
    """
    try:
        source = get_thermal_source()
        return thermal_source_status(source)
    except Exception:  # noqa: BLE001
        logger.exception("Thermal status probe failed")
        return {
            "mode": "unknown",
            "source": "unknown",
            "simulated": True,
            "hardware": "DEMO / SIMULATED THERMAL",
            "connected": False,
            "lifecycle": "ERROR",
            "quality": "INVALID",
            "measurement": "UNAVAILABLE",
            "ambient_c": None,
            "emissivity": None,
            "metadata": {},
            "disclaimer": "Thermal subsystem unavailable.",
        }
