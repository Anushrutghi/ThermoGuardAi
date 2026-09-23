"""ThermoGuard AI — FastAPI application entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import __version__
from backend.api.v1.router import api_router
from backend.core.config import get_settings
from backend.core.exceptions import register_exception_handlers
from backend.core.logging import setup_logging
from backend.db.session import init_db

settings = get_settings()
setup_logging()
logger = logging.getLogger(__name__)

# Runtime dirs must exist before StaticFiles mounts (which check at creation time)
settings.ensure_dirs()

# A weak/default JWT secret is a warning even in development so a misconfigured
# deployment is caught early; production refuses to start (validate_security).
if settings.secret_key == "change-me-to-a-long-random-string" or len(settings.secret_key) < 32:
    logger.warning(
        "SECRET_KEY is using the development default or is shorter than 32 characters — "
        "set a strong random value (>= 32 chars) before any production deployment."
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_dirs()
    # Fail fast on unsafe production configuration (weak secret / wildcard CORS).
    settings.validate_security()
    if settings.storage_uses_firestore:
        # Production persistence is Firestore — the local database is never
        # created, migrated or seeded. (S6 hard requirement.)
        logger.info("Firestore storage backend active — local database init/seed skipped")
    else:
        init_db()
        from backend.db.seed import seed_defaults

        seed_defaults()
    if settings.auth_uses_firebase:
        from backend.firebase.users import bootstrap_admin

        bootstrap_admin()
        logger.info("Firebase authentication backend active (project=%s)", settings.firebase_project_id)
    logger.info("%s v%s started (env=%s)", settings.app_name, __version__, settings.app_env)
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="ThermoGuard AI",
    version=__version__,
    description="Real-time intelligent electrical panel inspection, fault detection & predictive maintenance platform.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Production only: reject requests whose Host header is not explicitly allowed
# (mitigates DNS-rebinding / Host-header attacks). Development keeps the default
# permissive behavior so localhost/TestClient are unaffected.
if settings.app_env == "production":
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[h.strip() for h in settings.allowed_hosts.split(",") if h.strip()],
    )

register_exception_handlers(app)
app.include_router(api_router, prefix="/api/v1")


@app.get("/mobile", tags=["system"])
def mobile_client():
    """Serve the phone camera client page at the documented top-level URL."""
    from backend.api.v1.mobile import serve_mobile_client

    return serve_mobile_client()


@app.get("/health/live", tags=["system"])
def health_live() -> dict:
    """Liveness: the API process is up. Deliberately trivial."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["system"])
def health_ready() -> dict:
    """Readiness: the database accepts queries (real round-trip, not just 'up').

    With STORAGE_BACKEND=firestore the probe targets Firestore (a real read);
    the local database is never touched.
    """
    if settings.storage_uses_firestore:
        try:
            from backend.firebase.client import get_firestore

            get_firestore().collection("__counters").limit(1).get()
            return {"status": "ok", "database": "firestore"}
        except Exception:  # noqa: BLE001
            logger.exception("Firestore readiness probe failed")
            return JSONResponse(status_code=503, content={"status": "unavailable", "database": "error"})
    from sqlalchemy import text

    from backend.db.session import SessionLocal

    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            db.commit()
        finally:
            db.close()
        return {"status": "ok", "database": "ok"}
    except Exception:  # noqa: BLE001
        logger.exception("Readiness probe failed")
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "error"})


@app.get("/health", tags=["system"])
def health() -> dict:
    """Aggregate health: app up, database responding, thermal subsystem state.

    Thermal availability is reported honestly — the API process being up does
    not mean a thermal sensor is present; DEMO/SIMULATED is labeled as such.
    """
    from sqlalchemy import text

    from ai.detector.factory import get_detector
    from ai.thermal.factory import get_thermal_source, thermal_source_status
    from backend.db.session import SessionLocal

    detector = get_detector()
    thermal = get_thermal_source()
    thermal_status = thermal_source_status(thermal)

    # S5: thermal hardware health is reported separately from app/db health —
    # an API process being up never implies a thermal sensor is present.
    thermal_health = "ok" if thermal_status["measurement"] == "MEASURED" else "degraded"

    database_ok = True
    if settings.storage_uses_firestore:
        try:
            from backend.firebase.client import get_firestore

            get_firestore().collection("__counters").limit(1).get()
            database_backend = "firestore"
        except Exception:  # noqa: BLE001
            logger.exception("Health Firestore probe failed")
            database_ok = False
            database_backend = "firestore-error"
    else:
        database_backend = settings.database_url.split("://")[0]
        try:
            db = SessionLocal()
            try:
                db.execute(text("SELECT 1"))
                db.commit()
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            logger.exception("Health database probe failed")
            database_ok = False

    return {
        "status": "ok" if database_ok else "degraded",
        "app": settings.app_name,
        "version": __version__,
        "database": database_backend,
        "database_ok": database_ok,
        "detector": detector.engine,
        "thermal": thermal.name,
        "thermal_simulated": thermal_status["simulated"],
        "thermal_lifecycle": thermal_status["lifecycle"],
        "thermal_quality": thermal_status["quality"],
        "thermal_measurement": thermal_status["measurement"],
        "thermal_health": thermal_health,
        "thermal_metadata": thermal_status["metadata"],
    }


@app.get("/", tags=["system"])
def root() -> dict:
    return {
        "name": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
        "api": "/api/v1",
        "mobile": "/mobile",
    }


# Static media (captured frames, generated reports, mobile client page)
app.mount("/media", StaticFiles(directory=str(settings.media_path)), name="media")
app.mount("/reports", StaticFiles(directory=str(settings.report_path)), name="reports")
