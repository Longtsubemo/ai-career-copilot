"""Client for a Firecrawl scraping API.

All Firecrawl-specific HTTP details — endpoint, payload envelope, API-key
header and response-shape validation — live here. The Discovery Agent depends
only on :meth:`FirecrawlClient.scrape` and the :class:`CrawlResult` it
returns.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from unicodedata import normalize
from urllib.parse import unquote
from uuid import uuid4

import httpx

DEFAULT_TIMEOUT = 30.0
DEFAULT_SCRAPE_PATH = "/v2/scrape"
MAX_ERROR_BODY_BYTES = 16 * 1024
MAX_PROVIDER_MESSAGE_CHARS = 240
_SAFE_PROVIDER_ERROR_CODES = frozenset({"SCRAPE_ALL_ENGINES_FAILED"})
_REQUEST_ID_HEADERS = (
    "x-firecrawl-request-id",
    "x-request-id",
    "x-correlation-id",
    "x-correlationid",
    "request-id",
    "traceparent",
)
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_CORRELATION_ID = re.compile(r"^[0-9a-f]{32}$")
_PROVIDER_CODE_LIKE = re.compile(
    r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+(?![A-Za-z0-9])"
)
_ENCODED_PATH_DELIMITER = re.compile(r"%(?:25)*(?:2f|5c|23)", re.IGNORECASE)
_OPAQUE_SECRET = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-])"
)
_JWT_VALUE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"
    r"\.[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])"
)
_URL_IN_TEXT = re.compile(r'https?://[^\s<>"]+', re.IGNORECASE)
_QUERY_VALUE = re.compile(r"([?&][^=&#\s]{1,64}=)[^&#\s]*")
_AUTH_VALUE = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+"
)
_COOKIE_VALUE = re.compile(r"(?i)\b(cookie|set-cookie)\s*[:=]\s*[^\r\n]+")
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|token|secret|password|session(?:id)?)(\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_VALUE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+")
_STACK_TRACE = re.compile(
    r"(?i)traceback|stack trace|\bat [\w.$/\\-]+\([^\n]*:\d+\)"
)


class FirecrawlError(RuntimeError):
    """A scrape request failed at transport, API or response-shape level.

    ``kind`` is a coarse, factual failure class (``http_error``,
    ``timeout``, ``network_error``, ``api_error`` or ``shape_error``) and
    ``status_code`` carries the HTTP status when one was received, so the
    discovery agent can categorize a failure without parsing the message.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        kind: str | None = None,
        correlation_id: str | None = None,
        request_id: str | None = None,
        provider_error_code: str | None = None,
        provider_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind
        self.correlation_id = (
            correlation_id
            if isinstance(correlation_id, str)
            and _SAFE_CORRELATION_ID.fullmatch(correlation_id)
            else None
        )
        self.request_id = (
            request_id.strip()
            if isinstance(request_id, str)
            and _SAFE_REQUEST_ID.fullmatch(request_id.strip())
            else None
        )
        self.provider_error_code = _safe_error_code(provider_error_code)
        self.provider_message = _safe_provider_message(provider_message)


def _safe_request_id(headers: Mapping[str, str]) -> str | None:
    """Return a bounded opaque ID from a known correlation header only."""
    for header in _REQUEST_ID_HEADERS:
        value = headers.get(header)
        if isinstance(value, str):
            value = value.strip()
            if _SAFE_REQUEST_ID.fullmatch(value):
                return value
    return None


def _safe_error_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) > 64 or value not in _SAFE_PROVIDER_ERROR_CODES:
        return None
    return value


def _contains_path_or_fragment(value: str) -> bool:
    """Reject ambiguous path/fragment syntax after common decoding forms."""
    decoded = value
    for _ in range(3):
        decoded = unquote(decoded)
    decoded = normalize("NFKC", html.unescape(decoded))
    return (
        any(delimiter in decoded for delimiter in ("/", "\\", "#"))
        or _ENCODED_PATH_DELIMITER.search(decoded) is not None
    )


def _safe_provider_message(value: object) -> str | None:
    """Keep only short structured text after removing common sensitive data."""
    if not isinstance(value, str) or not value or len(value) > 1024:
        return None
    if any(char in value for char in "<>\r\n") or _STACK_TRACE.search(value):
        return None
    if any(
        match.group(0) not in _SAFE_PROVIDER_ERROR_CODES
        for match in _PROVIDER_CODE_LIKE.finditer(value)
    ):
        # Do not let an unrecognized code bypass the code-field allowlist by
        # being repeated in the provider's free-form message.
        return None

    # Absolute URLs can be safely removed as a unit. Any remaining path or
    # fragment delimiter is ambiguous free-form content, so reject the whole
    # message rather than risk retaining a sensitive relative path.
    message = _URL_IN_TEXT.sub("[url]", value)
    if _contains_path_or_fragment(message):
        return None

    message = _AUTH_VALUE.sub(r"\1[redacted]", message)
    message = _COOKIE_VALUE.sub(r"\1=[redacted]", message)
    message = _SENSITIVE_VALUE.sub(r"\1\2[redacted]", message)
    message = _BEARER_VALUE.sub(r"\1[redacted]", message)
    message = _QUERY_VALUE.sub(r"\1[redacted]", message)
    message = _JWT_VALUE.sub("[redacted]", message)
    message = _OPAQUE_SECRET.sub("[redacted]", message)
    message = re.sub(r"[\x00-\x1f\x7f]", " ", message)
    message = " ".join(message.split())
    if not message:
        return None
    return message[:MAX_PROVIDER_MESSAGE_CHARS]


def _provider_error_details(payload: object) -> tuple[str | None, str | None]:
    """Extract allowlisted error fields from a JSON object, never its raw body."""
    if not isinstance(payload, Mapping):
        return None, None
    error = payload.get("error")
    error_object = error if isinstance(error, Mapping) else {}
    code = payload.get("code")
    if code is None:
        code = error_object.get("code")
    message = error_object.get("message")
    if not isinstance(message, str):
        message = error if isinstance(error, str) else payload.get("message")
    return _safe_error_code(code), _safe_provider_message(message)


def _http_error_details(
    response: httpx.Response,
) -> tuple[str | None, str | None, str | None]:
    """Best-effort bounded diagnostics; callers must preserve the HTTP error."""
    request_id = _safe_request_id(response.headers)
    if len(response.content) > MAX_ERROR_BODY_BYTES:
        return request_id, None, None
    try:
        payload = response.json()
        code, message = _provider_error_details(payload)
    except Exception:
        # Diagnostic parsing must never mask the original HTTP status failure.
        return request_id, None, None
    return request_id, code, message


@dataclass(frozen=True)
class CrawlResult:
    """Clean page content plus any metadata the crawler reported."""

    url: str
    markdown: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


def parse_scrape_response(
    payload: object,
    *,
    requested_url: str,
    correlation_id: str | None = None,
    request_id: str | None = None,
) -> CrawlResult:
    """Parse a Firecrawl scrape body into a :class:`CrawlResult`.

    ``success=false`` and invalid shapes raise :class:`FirecrawlError`.
    Provider details, when safe, are attached as diagnostic attributes while
    the exception message remains generic for API-facing paths.
    """
    if not isinstance(payload, Mapping):
        raise FirecrawlError(
            f"scrape response is not a JSON object: {type(payload).__name__}",
            kind="shape_error",
            correlation_id=correlation_id,
            request_id=request_id,
        )
    if payload.get("success") is not True:
        code, message = _provider_error_details(payload)
        raise FirecrawlError(
            "scrape failed",
            kind="api_error",
            correlation_id=correlation_id,
            request_id=request_id,
            provider_error_code=code,
            provider_message=message,
        )
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise FirecrawlError(
            "scrape response has no 'data' object",
            kind="shape_error",
            correlation_id=correlation_id,
            request_id=request_id,
        )

    markdown = data.get("markdown")
    if not isinstance(markdown, str):
        markdown = data.get("content")
    if not isinstance(markdown, str):
        markdown = data.get("text")
    if not isinstance(markdown, str):
        markdown = None

    metadata = data.get("metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}

    return CrawlResult(url=requested_url, markdown=markdown, metadata=dict(metadata))


class FirecrawlClient:
    """Small HTTP client for a Firecrawl instance.

    ``transport`` is injectable so tests can drive the client with an
    ``httpx.MockTransport`` instead of the network. The API key, when set,
    is sent as a bearer token.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        scrape_path: str = DEFAULT_SCRAPE_PATH,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url must not be empty")
        if not scrape_path.startswith("/"):
            raise ValueError("scrape_path must start with '/'")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._scrape_path = scrape_path
        self._transport = transport

    @property
    def base_url(self) -> str:
        return self._base_url

    def scrape(self, url: str) -> CrawlResult:
        """Fetch ``url`` and return its clean content.

        Raises :class:`FirecrawlError` on transport failure, non-2xx
        responses, undecodable JSON, ``success=false`` or an invalid
        response shape.
        """
        correlation_id = uuid4().hex
        headers = {
            "Content-Type": "application/json",
            "External-Request-Id": correlation_id,
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {"url": url, "formats": ["markdown"]}

        failure: FirecrawlError | None = None
        response: httpx.Response | None = None
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.post(
                    f"{self._base_url}{self._scrape_path}",
                    json=body,
                    headers=headers,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            try:
                request_id, error_code, provider_message = _http_error_details(
                    exc.response
                )
            except Exception:
                # Keep the original status classification even if metadata access fails.
                request_id, error_code, provider_message = None, None, None
            failure = FirecrawlError(
                f"scrape returned HTTP {status_code}",
                status_code=status_code,
                kind="http_error",
                correlation_id=correlation_id,
                request_id=request_id,
                provider_error_code=error_code,
                provider_message=provider_message,
            )
        except httpx.TimeoutException:
            failure = FirecrawlError(
                "scrape request timed out",
                kind="timeout",
                correlation_id=correlation_id,
            )
        except httpx.HTTPError:
            failure = FirecrawlError(
                "scrape request failed",
                kind="network_error",
                correlation_id=correlation_id,
            )

        if failure is not None:
            # Raise outside the except block so transport exceptions (which may
            # contain request headers or response bodies) are not chained.
            raise failure
        assert response is not None

        try:
            payload = response.json()
        except ValueError:
            failure = FirecrawlError(
                "scrape response is not valid JSON",
                kind="shape_error",
                correlation_id=correlation_id,
                request_id=_safe_request_id(response.headers),
            )
        if failure is not None:
            raise failure
        return parse_scrape_response(
            payload,
            requested_url=url,
            correlation_id=correlation_id,
            request_id=_safe_request_id(response.headers),
        )
