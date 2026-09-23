"""Unit tests for password hashing and JWT."""
from __future__ import annotations

import jwt as pyjwt

from backend.core.security import create_access_token, decode_access_token, hash_password, verify_password


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("s3cret!")
    assert hashed != "s3cret!"
    assert verify_password("s3cret!", hashed)
    assert not verify_password("wrong", hashed)


def test_jwt_roundtrip() -> None:
    token = create_access_token(42, extra={"role": "technician"})
    payload = decode_access_token(token)
    assert payload["sub"] == "42"
    assert payload["role"] == "technician"


def test_jwt_invalid_token_raises() -> None:
    import pytest

    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token("not.a.token")
