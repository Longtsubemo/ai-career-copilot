"""Shared FastAPI dependencies of the API layer.

Two kinds live here: how a route reaches the per-app handler registry and
how it answers "who am I". Identity comes from the server-side session, and
the candidate is the one owned by that account — never "the one profile in
the table". The agents keep receiving explicit ids while the routes derive
them from the authenticated owner.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.agents.runtime import HandlerRegistry
from app.api.csrf import (
    CSRF_ERROR_DETAIL,
    SAFE_METHODS,
    session_bound_token_is_valid,
)
from app.clients.firecrawl import FirecrawlClient
from app.core.config import get_settings
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.candidate import Candidate
from app.models.user import User
from app.services import auth as auth_service
from app.services import candidates as candidate_service

NO_CANDIDATE_DETAIL = (
    "no candidate profile exists yet; create one with POST /api/candidates"
)
NO_MASTER_RESUME_DETAIL = (
    "no master resume uploaded yet; upload one with POST /api/resumes/master"
)
NOT_AUTHENTICATED_DETAIL = "authentication required"


def get_registry(request: Request) -> HandlerRegistry:
    """Return the handler registry this app executes agents with."""
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        raise HTTPException(status_code=500, detail="agent registry is not configured")
    return registry


def get_firecrawl(request: Request) -> FirecrawlClient:
    """Return the Firecrawl client this app scrapes pages with.

    A single client is built per app (settings-derived, or the injected fake)
    so discovery and re-verification always crawl through the same service.
    """
    client = getattr(request.app.state, "firecrawl", None)
    if client is None:
        raise HTTPException(status_code=500, detail="firecrawl client is not configured")
    return client


def get_current_session(
    request: Request, session: Session = Depends(get_db)
) -> AuthSession:
    """Return the live session named by the cookie, or raise 401.

    The cookie carries an opaque token; the lookup hashes it and rejects an
    unknown, revoked or expired session with the same 401 so the reason a
    credential failed is never disclosed. On a state-changing request the
    submitted CSRF token must also hash to the value stored on this session,
    so a forged cookie/header pair cannot pass merely by agreeing with itself.
    """
    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED_DETAIL)
    auth_session = auth_service.get_session_by_token(session, raw_token)
    if auth_session is None:
        raise HTTPException(status_code=401, detail=NOT_AUTHENTICATED_DETAIL)
    if (
        request.method not in SAFE_METHODS
        and not session_bound_token_is_valid(request, auth_session)
    ):
        raise HTTPException(status_code=403, detail=CSRF_ERROR_DETAIL)
    return auth_session


def get_current_user(
    auth_session: AuthSession = Depends(get_current_session),
) -> User:
    """Return the account behind the request's session."""
    return auth_session.user


def get_current_candidate(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> Candidate:
    """Return the candidate owned by the authenticated account, or 404.

    The lookup is by owner, so a second account can never receive the first
    account's profile; a missing profile is a 404, never a fallback to
    another user's candidate.
    """
    candidate = candidate_service.candidate_for_user(session, user.id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=NO_CANDIDATE_DETAIL)
    return candidate


__all__ = [
    "NO_CANDIDATE_DETAIL",
    "NO_MASTER_RESUME_DETAIL",
    "NOT_AUTHENTICATED_DETAIL",
    "get_current_candidate",
    "get_current_session",
    "get_current_user",
    "get_firecrawl",
    "get_registry",
]
