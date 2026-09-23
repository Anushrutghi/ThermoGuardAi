"""Firestore user profiles, organization registry and admin bootstrap (S6).

Firebase Authentication proves *identity* only. Role and organization are
loaded exclusively from the Firestore profile in ``users/{uid}`` — never from
client claims and never from client-supplied organization IDs. A brand-new
user is provisioned as ``role=viewer, organization=None``; an admin assigns
role/organization server-side.

Organization is a string (matching the S2 model) and the ``organizations``
collection is a registry of org names (no ORM model exists for it).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from backend.core.config import get_settings
from backend.core.exceptions import UnauthorizedError

logger = logging.getLogger(__name__)

PROFILES = "users"
USERNAMES = "usernames"
ORGANIZATIONS = "organizations"


@dataclass
class FirebaseUserProfile:
    """Attribute-compatible view of a Firestore user profile.

    Exposes the same surface the rest of the app reads off the SQLAlchemy
    ``User`` model: ``id`` (Firebase uid), ``username``, ``email``,
    ``full_name``, ``role``, ``organization``, ``is_active``, ``created_at``.
    """

    id: str
    username: str
    email: str | None = None
    full_name: str | None = None
    role: str = "viewer"
    organization: str | None = None
    is_active: bool = True
    created_at: datetime | None = None
    auth_provider: str = "firebase"


def _db():
    from backend.firebase.client import get_firestore

    return get_firestore()


def _profile_from_doc(uid: str, doc: object) -> FirebaseUserProfile:
    data = doc.to_dict() or {}
    created = data.get("created_at")
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created)
        except ValueError:
            created = None
    return FirebaseUserProfile(
        id=uid,
        username=data.get("username") or f"user_{uid[:8]}",
        email=data.get("email"),
        full_name=data.get("full_name"),
        role=data.get("role") or "viewer",
        organization=data.get("organization") or None,
        is_active=bool(data.get("is_active", True)),
        created_at=created,
        auth_provider=data.get("auth_provider") or "firebase",
    )


def get_profile(uid: str) -> FirebaseUserProfile | None:
    """Load the profile for a Firebase uid (None when it does not exist)."""
    if not uid:
        return None
    snap = _db().collection(PROFILES).document(uid).get()
    if snap is None or not snap.exists:
        return None
    return _profile_from_doc(uid, snap)


def _reserve_username(username: str, uid: str) -> bool:
    """Reserve a username → uid mapping (uniqueness). False on collision."""
    doc = _db().collection(USERNAMES).document(username)
    snap = doc.get()
    if snap is not None and snap.exists and (snap.to_dict() or {}).get("uid") != uid:
        return False
    doc.set({"uid": uid})
    return True


def _write_profile(profile: FirebaseUserProfile) -> None:
    db = _db()
    db.collection(PROFILES).document(profile.id).set(
        {
            "username": profile.username,
            "email": profile.email,
            "full_name": profile.full_name,
            "role": profile.role,
            "organization": profile.organization or "",
            "is_active": bool(profile.is_active),
            "created_at": (profile.created_at or datetime.now(UTC)).isoformat(),
            "auth_provider": profile.auth_provider,
        },
        merge=True,
    )


def provision_on_login(claims: dict) -> FirebaseUserProfile:
    """Return the profile for verified Firebase token claims, provisioning new users.

    New users are created as ``viewer`` with no organization — Firebase proves
    identity, it never grants organization access. Admin grants role/org later
    through the server-side profile API.
    """
    uid = claims.get("uid")
    if not uid:
        raise UnauthorizedError("Token missing uid")
    existing = get_profile(uid)
    if existing is not None:
        if not existing.is_active:
            logger.warning("Authentication denied for disabled Firebase account uid=%s", uid[:12])
            raise UnauthorizedError("Account is disabled")
        return existing
    email = claims.get("email") or ""
    display = claims.get("name") or email or ""
    base_username = email.split("@")[0] or f"user_{uid[:8]}"
    username = base_username if len(base_username) >= 3 else f"user_{uid[:8]}"
    suffix = 0
    candidate = username
    while not _reserve_username(candidate, uid):
        suffix += 1
        candidate = f"{username}_{suffix}"
    provider = (claims.get("firebase") or {}).get("sign_in_provider") or "firebase"
    profile = FirebaseUserProfile(
        id=uid,
        username=candidate,
        email=email or None,
        full_name=display or None,
        role="viewer",
        organization=None,
        is_active=True,
        created_at=datetime.now(UTC),
        auth_provider=provider,
    )
    _write_profile(profile)
    logger.info("Provisioned Firebase profile uid=%s username=%s role=viewer", uid[:12], candidate)
    return profile


def bootstrap_admin() -> None:
    """Create/refresh the seed admin in Firebase Auth + Firestore (idempotent).

    Runs at application startup when AUTH_BACKEND=firebase. Never logs the
    password; the seed password is only used to create the Auth user when the
    email does not exist yet.
    """
    settings = get_settings()
    email = settings.seed_admin_email
    if not email:
        logger.warning("Seed admin bootstrap skipped: SEED_ADMIN_EMAIL unset")
        return
    db = _db()
    import firebase_admin.auth as fauth

    try:
        user = fauth.get_user_by_email(email)
    except fauth.UserNotFoundError:
        user = fauth.create_user(
            email=email,
            password=settings.seed_admin_password,
            display_name=settings.seed_admin_username,
        )
        logger.info("Created Firebase Auth user for seed admin email=%s", email)
    org = settings.seed_organization or None
    if org:
        org_ref = db.collection(ORGANIZATIONS).document(org)
        snap = org_ref.get()
        if snap is None or not snap.exists:
            org_ref.set({"name": org, "created_at": datetime.now(UTC).isoformat()})
    existing = get_profile(user.uid)
    if existing is None or existing.role != "admin" or existing.organization != org:
        profile = FirebaseUserProfile(
            id=user.uid,
            username=settings.seed_admin_username,
            email=email,
            full_name=settings.seed_admin_username,
            role="admin",
            organization=org,
            is_active=True,
            created_at=existing.created_at if existing else datetime.now(UTC),
            auth_provider="seed",
        )
        _write_profile(profile)
        _reserve_username(settings.seed_admin_username, user.uid)
        logger.info("Bootstrapped admin profile email=%s uid=%s", email, user.uid[:12])


def list_profiles(limit: int = 500) -> list[FirebaseUserProfile]:
    """List all profiles (admin endpoint support)."""
    out: list[FirebaseUserProfile] = []
    for snap in _db().collection(PROFILES).limit(limit).get():
        out.append(_profile_from_doc(snap.id, snap))
    return out


def update_profile(
    uid: str,
    *,
    role: str | None = None,
    organization: str | None = None,
    is_active: bool | None = None,
    full_name: str | None = None,
) -> FirebaseUserProfile | None:
    """Server-side profile update (role/org assignment). Returns None if missing."""
    profile = get_profile(uid)
    if profile is None:
        return None
    if role is not None:
        profile.role = role
    if organization is not None:
        profile.organization = organization or None
    if is_active is not None:
        profile.is_active = bool(is_active)
    if full_name is not None:
        profile.full_name = full_name
    _write_profile(profile)
    return profile
