"""Database engine, session factory and FastAPI dependency.

Production persistence is Cloud Firestore (``STORAGE_BACKEND=firestore``). In
that mode the SQLAlchemy engine is **never created** and ``SessionLocal()``
raises immediately — the application can never silently fall back to a local
SQLite/PostgreSQL database. In development/tests (the default) the configured
(``DATABASE_URL``) engine is created lazily on first use.
"""
from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import get_settings
from backend.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Lightweight additive migrations for columns added after the initial release.
# create_all() only creates missing tables, so existing databases need these
# ALTER TABLE statements. New tables are created automatically by create_all.
_COLUMN_ADDITIONS: dict[str, list[tuple[str, str]]] = {
    "inspections": [
        ("inspection_code", "VARCHAR(32)"),
        ("software_version", "VARCHAR(32)"),
        ("model_version", "VARCHAR(64)"),
        ("archived", "BOOLEAN DEFAULT 0"),
        ("thermal_source", "VARCHAR(64)"),
        ("thermal_simulated", "BOOLEAN DEFAULT 0"),
        ("original_image_path", "TEXT"),
        ("annotated_image_path", "TEXT"),
        ("thermal_image_path", "TEXT"),
        ("device_id", "INTEGER"),
    ],
    "components": [
        ("code", "VARCHAR(16)"),
        ("installed_at", "DATE"),
        ("expected_life_years", "FLOAT DEFAULT 15.0"),
        ("replacement_count", "INTEGER DEFAULT 0"),
        ("last_replaced_at", "DATE"),
        ("retired", "BOOLEAN DEFAULT 0"),
    ],
    "maintenance_records": [
        ("component_id", "INTEGER"),
        ("cost", "FLOAT"),
        ("completed_at", "DATETIME"),
    ],
    "faults": [("component_id", "INTEGER")],
    # S2 device intelligence: metadata + derived operational status (additive only)
    "devices": [
        ("building", "VARCHAR(128)"),
        ("floor", "VARCHAR(64)"),
        ("room", "VARCHAR(128)"),
        ("rated_voltage", "FLOAT"),
        ("rated_current", "FLOAT"),
        ("last_maintenance_date", "DATE"),
        ("next_inspection_date", "DATE"),
        ("derived_status", "VARCHAR(32) DEFAULT 'ACTIVE'"),
        ("status_override", "BOOLEAN DEFAULT 0"),
    ],
    # S2 organization isolation
    "users": [("organization", "VARCHAR(128)")],
    # S4 panel/component ownership (legacy rows stay NULL = platform-level)
    "panels": [("organization", "VARCHAR(128)")],
    # S4 final hardening: emissivity of the sensor that actually produced the
    # measurement (None = unavailable). Additive — existing rows stay NULL.
    "thermal_history": [("emissivity", "FLOAT")],
    # S5 contract hardening: lifecycle, persistent component, delta_t, peer comparison, and resolution audit trail
    "incidents": [
        ("status", "VARCHAR(32) DEFAULT 'OPEN'"),
        ("stage", "VARCHAR(32) DEFAULT 'CONFIRMED'"),
        ("confidence", "FLOAT DEFAULT 1.0"),
        ("reasons_json", "TEXT"),
        ("assigned_to", "VARCHAR(64)"),
        ("acknowledged_at", "DATETIME"),
        ("acknowledged_by", "VARCHAR(64)"),
        ("original_severity", "VARCHAR(16)"),
        ("override_reason", "TEXT"),
        ("escalated_at", "DATETIME"),
        ("escalated_by", "VARCHAR(64)"),
        ("escalation_reason", "TEXT"),
        ("organization", "VARCHAR(128)"),
        ("device_id", "INTEGER"),
        ("panel_id", "INTEGER"),
        ("component_id", "INTEGER"),
        ("delta_t", "FLOAT"),
        ("peer_comparison", "TEXT"),
        ("evidence_thermal_path", "VARCHAR(512)"),
        ("resolved_at", "DATETIME"),
        ("resolved_by", "VARCHAR(64)"),
        ("resolution_notes", "TEXT"),
    ],
    # S5 contract hardening: separate simulated vs measured time-series
    "temperature_history": [("simulated", "BOOLEAN DEFAULT 0")],
    "reports": [
        ("snapshot_json", "TEXT"),
        ("snapshot_sha256", "VARCHAR(64)"),
        ("snapshot_version", "VARCHAR(32) DEFAULT '1.0'"),
    ],
}

# S2: indexes for org-scoped lookups and device analytics (idempotent).
_INDEX_ADDITIONS: dict[str, list[tuple[str, str]]] = {
    "devices": [("ix_devices_organization", "organization")],
    "thermal_history": [("ix_thermal_history_device_id", "device_id")],
    # S4: org-scoped panel lookups + per-inspection thermal history reads
    "panels": [("ix_panels_organization", "organization")],
    "inspections": [("ix_inspections_device_status", "device_id, status")],
    "maintenance_records": [("ix_maintenance_inspection", "inspection_id")],
    "incidents": [
        ("ix_incidents_status", "status"),
        ("ix_incidents_device_id", "device_id"),
        ("ix_incidents_panel_id", "panel_id"),
        ("ix_incidents_component_id", "component_id"),
    ],
    "temperature_history": [("ix_temperature_history_simulated", "simulated")],
}

# Human-readable component code prefixes (spec: B1, B2, R1…)
_COMPONENT_CODE_PREFIX: dict[str, str] = {
    "circuit_breaker": "B",
    "breaker": "B",
    "mcb": "B",
    "mccb": "B",
    "rccb": "B",
    "relay": "R",
    "contactor": "K",
    "fuse": "F",
    "busbar": "BB",
    "terminal": "T",
    "cable": "C",
    "transformer": "TR",
    "motor_starter": "MS",
    "disconnect_switch": "DS",
    "power_supply": "PS",
    "indicator_light": "IL",
    "panel_door": "PD",
    "warning_label": "WL",
}


def _ensure_columns() -> None:
    """Add any missing columns to existing tables (idempotent, additive only)."""
    engine = _get_engine()
    if settings.database_url.startswith("sqlite"):
        with engine.begin() as conn:
            existing_tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
            for table, columns in _COLUMN_ADDITIONS.items():
                if table not in existing_tables:
                    continue
                existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
                for name, ddl in columns:
                    if name not in existing:
                        conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
                        logger.info("Migrated: added %s.%s", table, name)
            for table, indexes in _INDEX_ADDITIONS.items():
                if table not in existing_tables:
                    continue
                for index_name, column in indexes:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"))
    else:
        with engine.begin() as conn:
            for table, columns in _COLUMN_ADDITIONS.items():
                for name, ddl in columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {ddl}"))
            for table, indexes in _INDEX_ADDITIONS.items():
                for index_name, column in indexes:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"))


def _backfill_codes() -> None:
    """Give existing rows stable codes (inspection codes, component codes)."""
    from datetime import datetime

    from backend.models.component import Component
    from backend.models.inspection import Inspection

    def _as_dt(value):  # noqa: ANN001
        """Raw text() queries return SQLite datetimes as strings."""
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return value

    engine = _get_engine()
    with engine.begin() as conn:
        # Inspections → TG-YYYYMMDD-NNNNN
        rows = conn.execute(
            text("SELECT id, started_at FROM inspections WHERE inspection_code IS NULL")
        ).fetchall()
        for row_id, started_at in rows:
            seq = f"{int(row_id):05d}"
            parsed = _as_dt(started_at)
            code = f"TG-{parsed:%Y%m%d}-{seq}" if parsed else f"TG-UNKNOWN-{seq}"
            conn.execute(text("UPDATE inspections SET inspection_code = :c WHERE id = :i"), {"c": code, "i": row_id})
        if rows:
            logger.info("Backfilled %d inspection codes", len(rows))

        # Components → prefix + per-(panel, type) sequence
        comps = conn.execute(
            text("SELECT id, panel_id, component_type FROM components WHERE code IS NULL ORDER BY panel_id, component_type, id")
        ).fetchall()
        counters: dict[tuple[int, str], int] = {}
        for cid, panel_id, ctype in comps:
            key = (panel_id, ctype or "other")
            counters[key] = counters.get(key, 0) + 1
            prefix = _COMPONENT_CODE_PREFIX.get(ctype or "", "X")
            conn.execute(
                text("UPDATE components SET code = :c WHERE id = :i"),
                {"c": f"{prefix}{counters[key]}", "i": cid},
            )
        if comps:
            logger.info("Backfilled %d component codes", len(comps))

        # Backfill installed_at for components that have one (set to panel creation date)
        comps_installed = conn.execute(
            text(
                """SELECT c.id FROM components c
                   JOIN panels p ON p.id = c.panel_id
                   WHERE c.installed_at IS NULL"""
            )
        ).fetchall()
        for (cid,) in comps_installed:
            created = conn.execute(text("SELECT p.created_at FROM panels p JOIN components c ON c.panel_id = p.id WHERE c.id = :i"), {"i": cid}).scalar()
            parsed = _as_dt(created)
            if parsed:
                conn.execute(
                    text("UPDATE components SET installed_at = :d WHERE id = :i"),
                    {"d": parsed.date().isoformat(), "i": cid},
                )
        if comps_installed:
            logger.info("Backfilled %d component install dates", len(comps_installed))

        # Link faults to components by matching label on the same panel (failure history)
        linked_faults = conn.execute(
            text(
                """UPDATE faults SET component_id = (
                       SELECT c.id FROM components c
                       JOIN inspections i ON i.panel_id = c.panel_id
                       WHERE i.id = faults.inspection_id AND c.label = faults.component_label
                       LIMIT 1
                   )
                   WHERE faults.component_id IS NULL AND faults.component_label IS NOT NULL"""
            )
        ).rowcount
        if linked_faults:
            logger.info("Linked %d faults to components", linked_faults)

        # Link maintenance records to components the same way (lifecycle history)
        linked_mt = conn.execute(
            text(
                """UPDATE maintenance_records SET component_id = (
                       SELECT c.id FROM components c
                       JOIN inspections i ON i.panel_id = c.panel_id
                       WHERE i.id = maintenance_records.inspection_id
                         AND c.label = maintenance_records.component_label
                       LIMIT 1
                   )
                   WHERE maintenance_records.component_id IS NULL
                     AND maintenance_records.component_label IS NOT NULL
                     AND maintenance_records.inspection_id IS NOT NULL"""
            )
        ).rowcount
        if linked_mt:
            logger.info("Linked %d maintenance records to components", linked_mt)

    # Invalidate ORM metadata caches so newly-added columns are visible
    for model in (Inspection, Component):
        inspect(engine).get_columns(model.__tablename__)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
    """Enable WAL + foreign keys for SQLite."""
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


engine: Engine | None = None


def _get_engine() -> Engine:
    """Return the SQLAlchemy engine, creating it lazily (development/tests only).

    When ``STORAGE_BACKEND=firestore`` the engine is never created: production
    persistence is Cloud Firestore and any SQLAlchemy access is a bug that must
    fail loudly instead of silently touching a local database.
    """
    global engine
    if settings.storage_uses_firestore:
        raise RuntimeError(
            "SQLAlchemy engine is forbidden when STORAGE_BACKEND=firestore — production persistence is Cloud Firestore"
        )
    if engine is None:
        engine = create_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
        )
    return engine


_session_factory: Any = None


def SessionLocal() -> Session:
    """Open a SQLAlchemy session against the configured local database.

    Development/tests only. With ``STORAGE_BACKEND=firestore`` this raises
    immediately — the application must never silently fall back to a local
    database when Firebase is active.
    """
    if settings.storage_uses_firestore:
        raise RuntimeError(
            "SQLite/SQLAlchemy SessionLocal is forbidden when STORAGE_BACKEND=firestore — production persistence is Cloud Firestore"
        )
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=_get_engine(), autocommit=False, autoflush=False, expire_on_commit=False
        )
    return _session_factory()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a persistence session.

    With STORAGE_BACKEND=firestore a FirestoreSession shim is yielded instead
    of a SQLAlchemy session, so the repository/service layer runs unchanged
    against Cloud Firestore and the local database is never touched.
    """
    if settings.storage_uses_firestore:
        from backend.firebase.engine import FirestoreSession

        db = FirestoreSession()
        try:
            yield db  # type: ignore[misc]
        finally:
            db.close()
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def open_session() -> tuple[Session, Callable[[], None]]:
    """Open a persistence session honoring STORAGE_BACKEND (long-lived paths).

    Used by the WebSocket inspection handler and the MJPEG stream, which cannot
    use FastAPI dependency injection. Returns ``(db, close)`` — ``close()`` must
    be called exactly once when the session is no longer needed. In firestore
    mode ``db`` is a ``FirestoreSession``; otherwise a SQLAlchemy session.
    """
    gen = get_db()
    try:
        db = next(gen)
    except StopIteration:
        raise RuntimeError("get_db() did not yield a session") from None
    return db, gen.close


def init_db() -> None:
    """Create all tables, apply additive migrations, backfill codes.

    With STORAGE_BACKEND=firestore this is a no-op: the production data store
    is Firestore and the application must never depend on a local database.
    """
    if settings.storage_uses_firestore:
        logger.info("Firestore storage backend active — local database init skipped")
        return
    from backend import models  # noqa: F401

    Base = __import__("backend.db.base", fromlist=["Base"]).Base
    Base.metadata.create_all(bind=_get_engine())
    _ensure_columns()
    _backfill_codes()
    logger.info("Database initialised (url=%s)", settings.database_url)
