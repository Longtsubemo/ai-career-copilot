"""Firecrawl hosted Search API adapter for the discovery search contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import httpx

from app.clients.search import SearchError, SearchResult

DEFAULT_TIMEOUT = 15.0
DEFAULT_RESULT_LIMIT = 20
MAX_RESULT_LIMIT = 100
MAX_TITLE_CHARS = 500
MAX_DESCRIPTION_CHARS = 2_000


class FirecrawlSearchError(SearchError):
    """A Firecrawl Search request failed; provider bodies are never retained."""


def parse_firecrawl_search_response(
    payload: object, *, result_limit: int = DEFAULT_RESULT_LIMIT
) -> list[SearchResult]:
    """Map ``data.web`` into discovery metadata, dropping malformed hits."""
    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        raise FirecrawlSearchError("Firecrawl Search returned an invalid response")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise FirecrawlSearchError("Firecrawl Search response has no data object")
    web_results = data.get("web")
    if (
        isinstance(web_results, (str, bytes))
        or not isinstance(web_results, Sequence)
    ):
        raise FirecrawlSearchError("Firecrawl Search response has no web results list")

    parsed: list[SearchResult] = []
    for entry in web_results[:result_limit]:
        if not isinstance(entry, Mapping):
            continue
        url = entry.get("url")
        title = entry.get("title")
        if not isinstance(url, str) or not url.strip():
            continue
        if not isinstance(title, str) or not title.strip():
            continue
        description = entry.get("description")
        parsed.append(
            SearchResult(
                url=url.strip(),
                title=title.strip()[:MAX_TITLE_CHARS],
                content=(
                    description.strip()[:MAX_DESCRIPTION_CHARS]
                    if isinstance(description, str) and description.strip()
                    else None
                ),
                engine="firecrawl",
            )
        )
    return parsed


class FirecrawlSearchClient:
    """HTTP adapter for Firecrawl's hosted ``POST /v2/search`` endpoint.

    Search-result descriptions are normalized as ``SearchResult.content``;
    the discovery validator still extracts vacancy evidence only from a
    separate Firecrawl Scrape response.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None,
        timeout: float = DEFAULT_TIMEOUT,
        result_limit: int = DEFAULT_RESULT_LIMIT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url must not be empty")
        if not api_key or not api_key.strip():
            raise ValueError("Firecrawl Search requires an API key")
        if not 1 <= result_limit <= MAX_RESULT_LIMIT:
            raise ValueError("result_limit is outside the supported bound")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._result_limit = result_limit
        self._transport = transport

    def search(self, query: str) -> list[SearchResult]:
        """Run one bounded web search; never expose provider bodies in errors."""
        try:
            with httpx.Client(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = client.post(
                    f"{self._base_url}/v2/search",
                    json={
                        "query": query,
                        "limit": self._result_limit,
                        "sources": ["web"],
                    },
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self._api_key}",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise FirecrawlSearchError(
                f"Firecrawl Search returned HTTP {exc.response.status_code}"
            ) from None
        except httpx.TimeoutException:
            raise FirecrawlSearchError("Firecrawl Search request timed out") from None
        except httpx.HTTPError:
            raise FirecrawlSearchError("Firecrawl Search request failed") from None

        try:
            payload = response.json()
        except ValueError:
            raise FirecrawlSearchError(
                "Firecrawl Search response is not valid JSON"
            ) from None
        return parse_firecrawl_search_response(
            payload, result_limit=self._result_limit
        )
