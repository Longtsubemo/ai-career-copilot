"""Authentication primitives: hashing, sessions, expiry and revocation."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate import Candidate
from app.models.user import User
from app.services import auth


def test_normalize_email_trims_and_lowercases() -> None:
    assert auth.normalize_email("  Ada@Example.COM ") == "ada@example.com"


def test_password_hash_round_trip() -> None:
    hashed = auth.hash_password("correct horse battery staple")

    assert hashed != "correct horse battery staple"
    assert auth.verify_password("correct horse battery staple", hashed) is True
    assert auth.verify_password("wrong password", hashed) is False


def test_verify_password_rejects_a_malformed_hash() -> None:
    assert auth.verify_password("anything", "not-a-valid-argon2-hash") is False


def test_hash_token_is_stable_and_not_the_raw_token() -> None:
    raw = "opaque-token-value"

    assert auth.hash_token(raw) == auth.hash_token(raw)
    assert auth.hash_token(raw) != raw
    assert len(auth.hash_token(raw)) == 64


def test_register_user_creates_linked_candidate(session: Session) -> None:
    user, auth_session, raw_token, raw_csrf = auth.register_user(
        session, email="Ada@Example.com", password="password123", name="Ada"
    )

    assert user.email == "ada@example.com"
    candidate = session.execute(
        select(Candidate).where(Candidate.user_id == user.id)
    ).scalar_one()
    assert candidate.name == "Ada"
    assert auth_session.user_id == user.id
    assert raw_token and raw_csrf


def test_register_user_rejects_duplicate_email_case_insensitively(
    session: Session,
) -> None:
    auth.register_user(session, email="ada@example.com", password="password123")

    with pytest.raises(auth.DuplicateEmailError):
        auth.register_user(session, email="ADA@example.com", password="password123")


def test_each_user_gets_one_candidate(session: Session) -> None:
    first, *_ = auth.register_user(session, email="a@example.com", password="password123")
    second, *_ = auth.register_user(session, email="b@example.com", password="password123")

    assert first.id != second.id
    first_candidates = session.execute(
        select(Candidate).where(Candidate.user_id == first.id)
    ).scalars().all()
    second_candidates = session.execute(
        select(Candidate).where(Candidate.user_id == second.id)
    ).scalars().all()
    assert len(first_candidates) == 1
    assert len(second_candidates) == 1


def test_session_lookup_requires_the_raw_token(session: Session) -> None:
    user, auth_session, raw_token, _csrf = auth.register_user(
        session, email="ada@example.com", password="password123"
    )

    found = auth.get_session_by_token(session, raw_token)
    assert found is not None
    assert found.id == auth_session.id
    assert found.token_hash == auth.hash_token(raw_token)
    assert found.token_hash != raw_token
    assert auth.get_session_by_token(session, "some-other-token") is None


def test_expired_session_is_not_returned(session: Session) -> None:
    user, auth_session, raw_token, _csrf = auth.register_user(
        session, email="ada@example.com", password="password123"
    )
    auth_session.expires_at = auth.utcnow() - timedelta(seconds=1)
    session.flush()

    assert auth.get_session_by_token(session, raw_token) is None


def test_revoked_session_is_not_returned(session: Session) -> None:
    user, auth_session, raw_token, _csrf = auth.register_user(
        session, email="ada@example.com", password="password123"
    )

    auth.revoke_session(session, auth_session)

    assert auth_session.revoked_at is not None
    assert auth.get_session_by_token(session, raw_token) is None


def test_authenticate_returns_user_for_valid_credentials(session: Session) -> None:
    user, *_ = auth.register_user(session, email="ada@example.com", password="password123")

    assert auth.authenticate(session, email="ADA@example.com", password="password123").id == user.id


def test_authenticate_rejects_wrong_password(session: Session) -> None:
    auth.register_user(session, email="ada@example.com", password="password123")

    with pytest.raises(auth.InvalidCredentialsError):
        auth.authenticate(session, email="ada@example.com", password="nope")


def test_authenticate_rejects_unknown_email(session: Session) -> None:
    with pytest.raises(auth.InvalidCredentialsError):
        auth.authenticate(session, email="nobody@example.com", password="password123")


def test_authenticate_rejects_inactive_user(session: Session) -> None:
    user, *_ = auth.register_user(session, email="ada@example.com", password="password123")
    user.is_active = False
    session.flush()

    with pytest.raises(auth.InactiveUserError):
        auth.authenticate(session, email="ada@example.com", password="password123")
