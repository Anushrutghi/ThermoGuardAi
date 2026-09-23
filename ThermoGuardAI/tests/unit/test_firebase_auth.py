"""Firebase Authentication integration tests (S6) — offline via mocks + fakes.

Covers the backend security model: Firebase proves identity, role/organization
come ONLY from the Firestore profile (never client claims), new users are
provisioned as viewer with no organization, and disabled accounts are rejected.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.core.config import get_settings
from backend.core.exceptions import UnauthorizedError
from backend.core.security import create_access_token
from tests.fakes.fake_firestore import FakeFirestore


@pytest.fixture
def fake_firestore(monkeypatch) -> FakeFirestore:
    fake = FakeFirestore()
    monkeypatch.setattr("backend.firebase.client.get_firestore", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Token verification (mocked Admin SDK)
# ---------------------------------------------------------------------------


def test_verify_firebase_id_token_ok(monkeypatch) -> None:  # noqa: ANN001
    import firebase_admin.auth as fauth

    monkeypatch.setattr(fauth, "verify_id_token", lambda token, check_revoked=False: {"uid": "u1", "email": "a@b.io"})
    from backend.firebase.auth import verify_firebase_id_token

    assert verify_firebase_id_token("tok")["uid"] == "u1"


def test_verify_firebase_id_token_rejects_invalid(monkeypatch) -> None:  # noqa: ANN001
    import firebase_admin.auth as fauth

    def _boom(*args, **kwargs):  # noqa: ANN002
        raise fauth.InvalidIdTokenError("bad")

    monkeypatch.setattr(fauth, "verify_id_token", _boom)
    from backend.firebase.auth import verify_firebase_id_token

    with pytest.raises(UnauthorizedError):
        verify_firebase_id_token("bad")


# ---------------------------------------------------------------------------
# Profile provisioning (identity ≠ organization access)
# ---------------------------------------------------------------------------


def test_provision_new_user_is_viewer_without_org(fake_firestore) -> None:  # noqa: ANN001
    from backend.firebase.users import get_profile, provision_on_login

    profile = provision_on_login({"uid": "u-new", "email": "tech@x.io", "firebase": {"sign_in_provider": "password"}})
    assert profile.id == "u-new"
    assert profile.role == "viewer"
    assert profile.organization is None
    assert profile.is_active is True
    # persisted and reusable
    again = provision_on_login({"uid": "u-new", "email": "tech@x.io"})
    assert again.role == "viewer"
    assert get_profile("u-new").username == "tech"


def test_provision_phone_user_without_email(fake_firestore) -> None:  # noqa: ANN001
    from backend.firebase.users import provision_on_login

    profile = provision_on_login({"uid": "u-phone", "firebase": {"sign_in_provider": "phone"}})
    assert profile.email is None
    assert profile.username.startswith("user_")


def test_disabled_profile_rejected(fake_firestore) -> None:  # noqa: ANN001
    from backend.firebase.users import provision_on_login, update_profile

    provision_on_login({"uid": "u-dis", "email": "d@x.io"})
    update_profile("u-dis", is_active=False)
    with pytest.raises(UnauthorizedError):
        provision_on_login({"uid": "u-dis", "email": "d@x.io"})


def test_update_profile_assigns_role_and_org(fake_firestore) -> None:  # noqa: ANN001
    from backend.firebase.users import get_profile, provision_on_login, update_profile

    provision_on_login({"uid": "u-a", "email": "a@x.io"})
    update_profile("u-a", role="admin", organization="ThermoGuard")
    profile = get_profile("u-a")
    assert profile.role == "admin"
    assert profile.organization == "ThermoGuard"


# ---------------------------------------------------------------------------
# Admin bootstrap (mocked Admin SDK user lookup/creation)
# ---------------------------------------------------------------------------


def test_bootstrap_admin_creates_profile_and_org(fake_firestore, monkeypatch) -> None:  # noqa: ANN001
    import firebase_admin.auth as fauth

    def _get_user_by_email(email):  # noqa: ANN001
        raise fauth.UserNotFoundError(email)

    monkeypatch.setattr(fauth, "get_user_by_email", _get_user_by_email)
    monkeypatch.setattr(fauth, "create_user", lambda **kw: SimpleNamespace(uid="seed-uid-123"))

    from backend.firebase.users import bootstrap_admin, get_profile

    bootstrap_admin()
    profile = get_profile("seed-uid-123")
    assert profile is not None
    assert profile.role == "admin"
    assert profile.organization == "ThermoGuard"
    assert profile.username == "admin"
    assert fake_firestore.get_doc("organizations", "ThermoGuard") is not None


# ---------------------------------------------------------------------------
# App-JWT decode branch in deps (AUTH_BACKEND=firebase)
# ---------------------------------------------------------------------------


def test_decode_and_get_user_firebase_branch(fake_firestore, monkeypatch) -> None:  # noqa: ANN001
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    from backend.api.deps import decode_and_get_user
    from backend.firebase.users import provision_on_login

    provision_on_login({"uid": "u-dec", "email": "dec@x.io"})
    token = create_access_token("u-dec", extra={"role": "viewer", "username": "dec"})
    user = decode_and_get_user(token, db=object())
    assert user.id == "u-dec"
    assert user.role == "viewer"

    with pytest.raises(UnauthorizedError):
        decode_and_get_user("garbage.token.value", db=object())

    # unknown uid → rejected
    unknown = create_access_token("no-such-uid")
    with pytest.raises(UnauthorizedError):
        decode_and_get_user(unknown, db=object())


def test_firebase_login_issues_app_token(fake_firestore, monkeypatch) -> None:  # noqa: ANN001
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_backend", "firebase")
    from backend.services.auth_service import AuthService

    monkeypatch.setattr(
        "backend.firebase.auth.verify_firebase_id_token",
        lambda tok: {"uid": "u-log", "email": "log@x.io", "firebase": {"sign_in_provider": "google.com"}},
    )
    token, profile = AuthService(db=object()).firebase_login("ID_TOKEN")
    assert token
    assert profile.id == "u-log"
    assert profile.role == "viewer"

    # when firebase auth is not enabled → rejected
    monkeypatch.setattr(settings, "auth_backend", "local")
    with pytest.raises(UnauthorizedError):
        AuthService(db=object()).firebase_login("ID_TOKEN")
