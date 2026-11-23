"""Reject browser requests from unrelated origins to the loopback API."""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
ORIGIN_ERROR_DETAIL = "request origin is not allowed"


def _origin_tuple(value: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    scheme = parsed.scheme.casefold()
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname.casefold(), port


def is_allowed_origin(origin: str, request: Request) -> bool:
    """Accept the explicit Vite origins or this exact loopback API origin."""
    settings = get_settings()
    if origin in settings.cors_origins:
        return True
    supplied = _origin_tuple(origin)
    request_host = request.url.hostname
    if supplied is None or not request_host or request_host.casefold() not in LOCAL_HOSTS:
        return False
    request_origin = _origin_tuple(f"{request.url.scheme}://{request.url.netloc}")
    return supplied[1] in LOCAL_HOSTS and supplied == request_origin


class LocalOriginMiddleware(BaseHTTPMiddleware):
    """Block cross-site browser access while allowing local non-browser clients."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path == "/api" or request.url.path.startswith("/api/"):
            fetch_site = request.headers.get("sec-fetch-site", "").casefold()
            origin = request.headers.get("origin")
            if (origin is not None and not is_allowed_origin(origin, request)) or (
                origin is None and fetch_site == "cross-site"
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": ORIGIN_ERROR_DETAIL},
                )
        return await call_next(request)


__all__ = ["LocalOriginMiddleware", "ORIGIN_ERROR_DETAIL", "is_allowed_origin"]
