"""HTTP behaviour of the authentication endpoints."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth_session import AuthSession
from app.models.candidate import Candidate
from app.services import auth

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"
LOGOUT = "/api/auth/logout"
ME = "/api/auth/me"

SESSION_COOKIE = "career_copilot_session"
CSRF_COOKIE = "career_copilot_csrf"


def _register(client, email: str = "ada@example.com", name: str = "Ada"):
    return client.post(
        REGISTER, json={"email": email, "password": "password123", "name": name}
    )


def _cookie_line(response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    return ""


def test_register_creates_a_linked_candidate(client, session: Session) -> None:
    response = _register(client, email="Ada@Example.com")

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@example.com"
    candidate = session.execute(
        select(Candidate).where(Candidate.user_id == UUID(body["id"]))
    ).scalar_one()
    assert candidate.name == "Ada"


def test_register_rejects_a_duplicate_email(client) -> None:
    assert _register(client).status_code == 201
    client.cookies.clear()

    duplicate = _register(client, email="ADA@example.com")

    assert duplicate.status_code == 409


def test_register_rejects_a_short_password(client) -> None:
    response = client.post(
        REGISTER, json={"email": "ada@example.com", "password": "short"}
    )

    assert response.status_code == 422


def test_login_accepts_valid_credentials_and_sets_cookies(client) -> None:
    _register(client)
    client.cookies.clear()

    response = client.post(
        LOGIN, json={"email": "ada@example.com", "password": "password123"}
    )

    assert response.status_code == 200
    session_cookie = _cookie_line(response, SESSION_COOKIE)
    csrf_cookie = _cookie_line(response, CSRF_COOKIE)
    assert "httponly" in session_cookie.lower()
    assert "samesite=lax" in session_cookie.lower()
    assert "httponly" not in csrf_cookie.lower()
    assert csrf_cookie


def test_login_rejects_a_wrong_password(client) -> None:
    _register(client)
    client.cookies.clear()

    response = client.post(
        LOGIN, json={"email": "ada@example.com", "password": "definitely-wrong"}
    )

    assert response.status_code == 401


def test_login_rejects_an_unknown_email(client) -> None:
    response = client.post(
        LOGIN, json={"email": "nobody@example.com", "password": "password123"}
    )

    assert response.status_code == 401


def test_me_requires_authentication(client) -> None:
    assert client.get(ME).status_code == 401


def test_me_returns_the_current_user(client) -> None:
    _register(client, email="ada@example.com")

    response = client.get(ME)

    assert response.status_code == 200
    assert response.json()["email"] == "ada@example.com"


def test_two_users_browse_with_distinct_sessions(client_factory) -> None:
    ada = client_factory()
    grace = client_factory()
    _register(ada, email="ada@example.com")
    _register(grace, email="grace@example.com")

    assert ada.get(ME).json()["email"] == "ada@example.com"
    assert grace.get(ME).json()["email"] == "grace@example.com"


def test_logout_requires_a_csrf_token(client) -> None:
    _register(client)

    response = client.post(LOGOUT)

    assert response.status_code == 403


def test_logout_revokes_the_session(client) -> None:
    _register(client)
    csrf = client.cookies.get(CSRF_COOKIE)

    response = client.post(LOGOUT, headers={"X-CSRF-Token": csrf})

    assert response.status_code == 204
    assert client.get(ME).status_code == 401


def test_expired_session_is_rejected(client, session: Session) -> None:
    _register(client)
    stored = session.execute(select(AuthSession)).scalars().one()
    stored.expires_at = auth.utcnow() - timedelta(seconds=1)
    session.flush()

    assert client.get(ME).status_code == 401
