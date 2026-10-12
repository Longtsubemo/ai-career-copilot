"""Authentication endpoints: register, login, logout and identity.

Registration writes one user and one candidate profile together; login
exchanges credentials for an opaque session. The raw session token and CSRF
token are set as cookies and never returned in the response body.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_session, get_current_user
from app.api.schemas import LoginRequest, RegisterRequest, UserOut
from app.core.config import get_settings
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.user import User
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_auth_cookies(
    response: Response, *, raw_token: str, raw_csrf: str
) -> None:
    """Attach the HttpOnly session cookie and the readable CSRF cookie."""
    settings = get_settings()
    max_age = settings.session_ttl_seconds
    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        max_age=max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        raw_csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    """Expire both auth cookies."""
    settings = get_settings()
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )
    response.delete_cookie(
        settings.csrf_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )


@router.post("/register", response_model=UserOut, status_code=201)
def register(
    body: RegisterRequest,
    response: Response,
    session: Session = Depends(get_db),
) -> User:
    """Create an account with its candidate profile and start a session."""
    user, _auth_session, raw_token, raw_csrf = auth_service.register_user(
        session,
        email=body.email,
        password=body.password,
        name=body.name,
    )
    session.commit()
    _set_auth_cookies(response, raw_token=raw_token, raw_csrf=raw_csrf)
    return user


@router.post("/login", response_model=UserOut)
def login(
    body: LoginRequest,
    response: Response,
    session: Session = Depends(get_db),
) -> User:
    """Verify credentials and start a new session."""
    user = auth_service.authenticate(session, email=body.email, password=body.password)
    _auth_session, raw_token, raw_csrf = auth_service.create_session(session, user.id)
    session.commit()
    _set_auth_cookies(response, raw_token=raw_token, raw_csrf=raw_csrf)
    return user


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    auth_session: AuthSession = Depends(get_current_session),
    session: Session = Depends(get_db),
) -> Response:
    """Revoke the current session and clear its cookies."""
    auth_service.revoke_session(session, auth_session)
    session.commit()
    _clear_auth_cookies(response)
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def read_current_user(user: User = Depends(get_current_user)) -> User:
    """Return the account behind the current session."""
    return user


__all__ = ["router"]
