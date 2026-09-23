"""API v1 router aggregator."""
from __future__ import annotations

from fastapi import APIRouter

from backend.api.v1 import (
    alarms,
    analytics,
    anomalies,
    assets,
    auth,
    cameras,
    devices,
    inspections,
    maintenance,
    mobile,
    panels,
    reports,
    thermal,
    users,
    ws,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(cameras.router)
api_router.include_router(panels.router)
api_router.include_router(devices.router)
api_router.include_router(inspections.router)
api_router.include_router(alarms.router)
api_router.include_router(reports.router)
api_router.include_router(analytics.router)
api_router.include_router(anomalies.router)
api_router.include_router(users.router)
api_router.include_router(maintenance.router)
api_router.include_router(assets.router)
api_router.include_router(mobile.router)
api_router.include_router(thermal.router)
api_router.include_router(ws.router)
