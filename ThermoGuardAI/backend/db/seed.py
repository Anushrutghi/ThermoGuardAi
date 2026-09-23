"""Seed default data: admin user and a demo panel with components."""
from __future__ import annotations

import logging

from backend.core.config import get_settings
from backend.core.security import hash_password
from backend.db.session import SessionLocal, init_db
from backend.models.component import Component
from backend.models.panel import Panel
from backend.models.user import User

logger = logging.getLogger(__name__)


def seed_defaults() -> None:
    """Idempotently seed admin user + demo panel."""
    init_db()
    settings = get_settings()
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == settings.seed_admin_username).first()
        if admin is None:
            db.add(
                User(
                    username=settings.seed_admin_username,
                    email=settings.seed_admin_email,
                    full_name="System Administrator",
                    hashed_password=hash_password(settings.seed_admin_password),
                    role="admin",
                    organization=settings.seed_organization,
                )
            )
            logger.info("Seeded admin user '%s'", settings.seed_admin_username)
        elif admin.organization is None:
            # S2 backfill: assign the default organization to a pre-existing
            # admin so organization isolation applies to every user. Metadata
            # only — inspection/thermal history is never touched.
            admin.organization = settings.seed_organization
            logger.info("Assigned organization '%s' to admin '%s'", settings.seed_organization, admin.username)

        if db.query(Panel).filter(Panel.code == "PANEL-MAIN").first() is None:
            panel = Panel(
                name="Main Distribution Panel",
                code="PANEL-MAIN",
                location="Electrical Room 1 / Ground Floor",
                description="Primary low-voltage distribution board (demo).",
            )
            db.add(panel)
            db.flush()
            demo_components = [
                ("Breaker B1", "circuit_breaker", 0.08, 0.12, 0.12, 0.3),
                ("Breaker B2", "circuit_breaker", 0.24, 0.12, 0.12, 0.3),
                ("Fuse F1", "fuse", 0.42, 0.15, 0.08, 0.2),
                ("Relay R3", "relay", 0.58, 0.14, 0.12, 0.26),
                ("Contactor C1", "contactor", 0.76, 0.15, 0.12, 0.28),
                ("Busbar", "busbar", 0.1, 0.6, 0.7, 0.08),
                ("Terminal T1", "terminal", 0.15, 0.75, 0.2, 0.12),
                ("Cable feed", "cable", 0.55, 0.82, 0.3, 0.08),
            ]
            for label, ctype, x, y, w, h in demo_components:
                db.add(Component(panel_id=panel.id, label=label, component_type=ctype, x=x, y=y, w=w, h=h))
            logger.info("Seeded demo panel PANEL-MAIN")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_defaults()
    print("Seeding complete. Admin login:", get_settings().seed_admin_username, "/", get_settings().seed_admin_password)
