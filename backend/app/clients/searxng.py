"""Client for a self-hosted SearXNG search instance.

All transport concerns — URL construction, HTTP, JSON decoding and
response-shape validation — live here. The Discovery Agent depends only on
:meth:`SearXNGClient.search` and the normalized :class:`SearchResult` objects
it returns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from urllib.parse import urlencode

import httpx

from app.clients.search import SearchError, SearchResult

DEFAULT_TIMEOUT = 15.0


class SearXNGError(SearchError):
    """A search request failed at transport, API or response-shape level."""


def build_search_url(base_url: str, query: str, *, page: int = 1) -> str:
    """Return the deterministic SearXNG JSON endpoint URL for ``query``."""
    if not base_url:
        raise ValueError("base_url must not be empty")
    if page < 1:
        raise ValueError("page must be >= 1")
    endpoint = base_url.rstrip("/") + "/search"
    params = urlencode({"q": query, "format": "json", "pageno": str(page)})
    return f"{endpoint}?{params}"


def parse_search_response(payload: object) -> list[SearchResult]:
    """Parse a SearXNG ``/search?format=json`` body into search results.

    The container shape is validated strictly: the body must be an object
    carrying a ``results`` list. Individual malformed entries are dropped so
    one broken hit never fails an otherwise valid response.
    """
    if not isinstance(payload, Mapping):
        raise SearXNGError(
            f"search response is not a JSON object: {type(payload).__name__}"
        )
    results = payload.get("results")
    if isinstance(results, (str, bytes)) or not isinstance(results, Sequence):
        raise SearXNGError("search response has no 'results' list")

    parsed: list[SearchResult] = []
    for entry in results:
        if not isinstance(entry, Mapping):
            continue
        url = entry.get("url")
        title = entry.get("title")
        if not isinstance(url, str) or not url.strip():
            continue
        if not isinstance(title, str) or not title.strip():
            continue
        content = entry.get("content")
        engine = entry.get("engine")
        parsed.append(
            SearchResult(
                url=url.strip(),
                title=title.strip(),
                content=content.strip()
                if isinstance(content, str) and content.strip()
                else None,
                engine=engine.strip()
                if isinstance(engine, str) and engine.strip()
                else None,
            )
        )
    return parsed


class SearXNGClient:
    """Small HTTP client for a self-hosted SearXNG instance.

    ``transport`` is injectable so tests can drive the client with an
    ``httpx.MockTransport`` instead of the network.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url must not be empty")
        self._base_url = base_url
        self._timeout = timeout
        self._transport = transport

    @property
    def base_url(self) -> str:
        return self._base_url

    def search(self, query: str, *, page: int = 1) -> list[SearchResult]:
        """Run ``query`` and return normalized results.

        Raises :class:`SearXNGError` on transport failure, non-2xx
        responses, undecodable JSON or an unexpected response shape.
        """
        url = build_search_url(self._base_url, query, page=page)
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.get(url, headers={"Accept": "application/json"})
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SearXNGError(
                f"search returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise SearXNGError(f"search request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise SearXNGError("search response is not valid JSON") from exc
        return parse_search_response(payload)
