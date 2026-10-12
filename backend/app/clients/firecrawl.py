"""Client for a Firecrawl scraping API.

All Firecrawl-specific HTTP details — endpoint, payload envelope, API-key
header and response-shape validation — live here. The Discovery Agent depends
only on :meth:`FirecrawlClient.scrape` and the :class:`CrawlResult` it
returns.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx

DEFAULT_TIMEOUT = 30.0
DEFAULT_SCRAPE_PATH = "/v2/scrape"


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
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind


@dataclass(frozen=True)
class CrawlResult:
    """Clean page content plus any metadata the crawler reported."""

    url: str
    markdown: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


def parse_scrape_response(payload: object, *, requested_url: str) -> CrawlResult:
    """Parse a Firecrawl scrape body into a :class:`CrawlResult`.

    ``success=false`` and invalid shapes raise :class:`FirecrawlError` with
    the API's own error message where available, so callers see why the page
    could not be fetched.
    """
    if not isinstance(payload, Mapping):
        raise FirecrawlError(
            f"scrape response is not a JSON object: {type(payload).__name__}"
        )
    if payload.get("success") is not True:
        detail = payload.get("error")
        message = detail if isinstance(detail, str) and detail else "success=false"
        raise FirecrawlError(f"scrape failed: {message}", kind="api_error")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise FirecrawlError(
            "scrape response has no 'data' object", kind="shape_error"
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
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {"url": url, "formats": ["markdown"]}

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
            raise FirecrawlError(
                f"scrape returned HTTP {exc.response.status_code}",
                status_code=exc.response.status_code,
                kind="http_error",
            ) from exc
        except httpx.TimeoutException as exc:
            raise FirecrawlError(
                f"scrape request timed out: {exc}", kind="timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise FirecrawlError(
                f"scrape request failed: {exc}", kind="network_error"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise FirecrawlError(
                "scrape response is not valid JSON", kind="shape_error"
            ) from exc
        return parse_scrape_response(payload, requested_url=url)
