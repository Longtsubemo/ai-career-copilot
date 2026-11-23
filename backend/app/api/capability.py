"""Per-process bearer capability for the desktop API."""

from __future__ import annotations

import hmac
import re

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

API_CAPABILITY_LENGTH = 64
_CAPABILITY_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_UNAUTHORIZED_DETAIL = "a valid local application capability is required"


def is_valid_api_capability(value: object) -> bool:
    return isinstance(value, str) and _CAPABILITY_RE.fullmatch(value) is not None


def _requires_capability(path: str) -> bool:
    return (
        path == "/api"
        or path.startswith("/api/")
        or path == "/health"
        or path.startswith("/health/")
        or path in {"/docs", "/redoc", "/openapi.json"}
    )


class ApiCapabilityMiddleware(BaseHTTPMiddleware):
    """Authenticate every API, health, and schema request in desktop mode."""

    def __init__(self, app, *, api_capability: str | None) -> None:
        super().__init__(app)
        self._api_capability = (
            api_capability if is_valid_api_capability(api_capability) else None
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not _requires_capability(request.url.path):
            return await call_next(request)

        authorization = request.headers.get("authorization", "")
        scheme, separator, supplied = authorization.partition(" ")
        expected = self._api_capability
        authorized = (
            expected is not None
            and separator == " "
            and scheme.casefold() == "bearer"
            and is_valid_api_capability(supplied)
            and hmac.compare_digest(supplied, expected)
        )
        if not authorized:
            return JSONResponse(
                status_code=401,
                content={"detail": _UNAUTHORIZED_DETAIL},
                headers={
                    "Cache-Control": "no-store",
                    "WWW-Authenticate": "Bearer",
                },
            )
        return await call_next(request)


__all__ = ["ApiCapabilityMiddleware", "is_valid_api_capability"]
