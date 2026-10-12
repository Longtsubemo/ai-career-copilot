"""Deterministic authentication primitives.

Password material is verified with Argon2id; an opaque session token is
returned to the client once and stored only as a SHA-256 hash, so the database
never holds a usable credential. Nothing here commits — the caller owns the
transaction, matching the rest of the service layer.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth_session import AuthSession
from app.models.candidate import Candidate
from app.models.user import User

SESSION_TTL = timedelta(days=7)

_password_hasher = PasswordHasher()

_DUMMY_PASSWORD_HASH = _password_hasher.hash("dummy-password-for-timing-equalization")


class DuplicateEmailError(LookupError):
    """An account with the normalized email already exists."""


class InvalidCredentialsError(LookupError):
    """The email/password pair did not authenticate."""


class InactiveUserError(LookupError):
    """The account exists but is disabled."""


def normalize_email(email: str) -> str:
    """Return the canonical, case-insensitive form of an email address."""
    return email.strip().lower()


def hash_password(password: str) -> str:
    """Return an Argon2id hash of ``password``."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return whether ``password`` matches ``password_hash``.

    A malformed stored hash is treated as a failed verification rather than an
    error, so a corrupted column can never crash a login attempt.
    """
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    """Return the hex SHA-256 digest stored for an opaque token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def find_user_by_email(session: Session, email: str) -> User | None:
    """Return the user owning the normalized ``email``, if any."""
    normalized = normalize_email(email)
    stmt = select(User).where(User.email == normalized)
    return session.execute(stmt).scalar_one_or_none()


def create_session(
    session: Session,
    user_id: UUID,
    *,
    ttl: timedelta = SESSION_TTL,
    now: datetime | None = None,
) -> tuple[AuthSession, str, str]:
    """Persist a session and return it with its raw token and CSRF token.

    Only hashes are stored; the raw values are returned once so the route can
    set them as cookies. Callers must not log the raw values.
    """
    issued_at = now or utcnow()
    raw_token = _new_token()
    raw_csrf = _new_token()
    auth_session = AuthSession(
        user_id=user_id,
        token_hash=hash_token(raw_token),
        csrf_token_hash=hash_token(raw_csrf),
        expires_at=issued_at + ttl,
    )
    session.add(auth_session)
    session.flush()
    return auth_session, raw_token, raw_csrf


def get_session_by_token(
    session: Session,
    raw_token: str,
    *,
    now: datetime | None = None,
) -> AuthSession | None:
    """Return the live session for ``raw_token``, or None.

    None is returned for an unknown, revoked or expired token so the caller
    cannot distinguish the three.
    """
    stmt = select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token))
    auth_session = session.execute(stmt).scalar_one_or_none()
    if auth_session is None:
        return None
    if auth_session.revoked_at is not None:
        return None
    if auth_session.expires_at <= (now or utcnow()):
        return None
    return auth_session


def revoke_session(session: Session, auth_session: AuthSession) -> None:
    """Mark one session as revoked; revocation is immediate and permanent."""
    auth_session.revoked_at = utcnow()
    session.flush()


def authenticate(session: Session, *, email: str, password: str) -> User:
    """Return the user for valid credentials or raise a domain error.

    A missing account still runs a hash verification against a dummy hash so
    the response time does not reveal whether the email exists.
    """
    user = find_user_by_email(session, email)
    if user is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise InvalidCredentialsError("invalid email or password")
    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("invalid email or password")
    if not user.is_active:
        raise InactiveUserError("this account is disabled")
    return user


def _default_candidate_name(normalized_email: str) -> str:
    local = normalized_email.split("@", 1)[0].strip()
    return local or normalized_email


def register_user(
    session: Session,
    *,
    email: str,
    password: str,
    name: str | None = None,
) -> tuple[User, AuthSession, str, str]:
    """Create a user and its candidate profile, then open a session.

    The account and profile are written in one transaction; the caller
    commits. One candidate per user is enforced by the unique ``user_id``.
    Returns the user, the session and the two raw cookie tokens.
    """
    normalized = normalize_email(email)
    if find_user_by_email(session, normalized) is not None:
        raise DuplicateEmailError(f"an account already exists for {normalized}")

    user = User(email=normalized, password_hash=hash_password(password))
    session.add(user)
    session.flush()

    candidate_name = name.strip() if name and name.strip() else _default_candidate_name(
        normalized
    )
    candidate = Candidate(name=candidate_name, user_id=user.id)
    session.add(candidate)
    session.flush()

    auth_session, raw_token, raw_csrf = create_session(session, user.id)
    return user, auth_session, raw_token, raw_csrf


__all__ = [
    "DuplicateEmailError",
    "InactiveUserError",
    "InvalidCredentialsError",
    "SESSION_TTL",
    "authenticate",
    "create_session",
    "find_user_by_email",
    "get_session_by_token",
    "hash_password",
    "hash_token",
    "normalize_email",
    "register_user",
    "revoke_session",
    "utcnow",
    "verify_password",
]
