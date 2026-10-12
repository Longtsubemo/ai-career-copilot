"""CSRF defence for cookie-authenticated state-changing requests.

Two checks guard an unsafe request that carries a session cookie. The middleware
first enforces the double-submit pattern: the ``X-CSRF-Token`` header must equal
the readable ``csrf`` cookie, so a cross-site page cannot forge a matching pair
blindly. The authenticated dependency then binds the request to the session:
the submitted token's hash must equal the one stored on that session row, so a
forged cookie/header pair — even a self-consistent one — cannot ride an existing
session. Requests with no session cookie are left to the authentication layer.
"""

from __future__ import annotations

import hmac
import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.models.auth_session import AuthSession
from app.services.auth import hash_token

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_ERROR_DETAIL = "CSRF token missing or invalid"


def _tokens_match(header_token: str | None, cookie_token: str | None) -> bool:
    if not header_token or not cookie_token:
        return False
    return secrets.compare_digest(header_token, cookie_token)


def session_bound_token_is_valid(
    request: Request, auth_session: AuthSession
) -> bool:
    """Return whether the submitted token matches the session's stored hash.

    The comparison is constant-time and hashes the submitted token the same way
    it was stored, so a token that merely equals a forged cookie is rejected.
    """
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not header_token:
        return False
    return hmac.compare_digest(hash_token(header_token), auth_session.csrf_token_hash)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Reject unsafe requests whose session token has no matching CSRF token."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        settings = get_settings()
        if request.method not in SAFE_METHODS and request.cookies.get(
            settings.session_cookie_name
        ):
            header_token = request.headers.get(CSRF_HEADER_NAME)
            cookie_token = request.cookies.get(settings.csrf_cookie_name)
            if not _tokens_match(header_token, cookie_token):
                return JSONResponse(status_code=403, content={"detail": CSRF_ERROR_DETAIL})
        return await call_next(request)


__all__ = [
    "CSRF_ERROR_DETAIL",
    "CSRF_HEADER_NAME",
    "CSRFMiddleware",
    "session_bound_token_is_valid",
]
