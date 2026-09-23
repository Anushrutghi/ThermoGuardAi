"""Team / user management routes (S3).

Organization isolation (S2.1/S3 §21): a user can only ever see members of their
own organization (NULL matches NULL — an organization-less admin sees only
organization-less users). Listing is admin-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import require_roles
from backend.db.session import get_db
from backend.models.user import User

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
def list_users(
    db: Session = Depends(get_db),  # noqa: B008
    admin: User = Depends(require_roles("admin")),  # noqa: B008
) -> dict:
    """Org-scoped user list for the Team page (admin-only)."""
    stmt = select(User).order_by(User.username.asc())
    if admin.organization is None:
        stmt = stmt.where(User.organization.is_(None))
    else:
        stmt = stmt.where(User.organization == admin.organization)
    users = db.scalars(stmt).all()
    return {
        "items": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role,
                "is_active": u.is_active,
                "organization": u.organization,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
        "total": len(users),
    }
