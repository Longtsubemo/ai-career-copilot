"""Offline tests for the Firecrawl Search adapter."""

from __future__ import annotations

import json

import httpx
import pytest

from app.clients.firecrawl_search import (
    DEFAULT_RESULT_LIMIT,
    FirecrawlSearchClient,
    FirecrawlSearchError,
    parse_firecrawl_search_response,
)
from app.agents.discovery.agent import register_discovery_handler
from app.agents.runtime import HandlerRegistry
from app.clients.firecrawl import FirecrawlClient
from app.core.config import Settings

BASE_URL = "https://firecrawl.test"
API_KEY = "test-only-api-key"


def make_client(handler, **kwargs: object) -> FirecrawlSearchClient:
    return FirecrawlSearchClient(
        BASE_URL,
        api_key=API_KEY,
        transport=httpx.MockTransport(handler),
        **kwargs,  # type: ignore[arg-type]
    )


def test_search_posts_bounded_web_query_and_maps_only_search_metadata() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "web": [
                        {
                            "url": "https://boards.greenhouse.io/example/jobs/123",
                            "title": "AI Engineer",
                            "description": "Search-only summary",
                            "markdown": "Never used as page evidence",
                        }
                    ]
                },
            },
        )

    result = make_client(handler).search("AI Engineer jobs site:boards.greenhouse.io")

    assert seen["method"] == "POST"
    assert seen["url"] == f"{BASE_URL}/v2/search"
    assert seen["authorization"] == f"Bearer {API_KEY}"
    assert seen["body"] == {
        "query": "AI Engineer jobs site:boards.greenhouse.io",
        "limit": DEFAULT_RESULT_LIMIT,
        "sources": ["web"],
    }
    assert [(hit.url, hit.title, hit.content, hit.engine) for hit in result] == [
        (
            "https://boards.greenhouse.io/example/jobs/123",
            "AI Engineer",
            "Search-only summary",
            "firecrawl",
        )
    ]


def test_parser_bounds_results_and_text_fields() -> None:
    parsed = parse_firecrawl_search_response(
        {
            "success": True,
            "data": {
                "web": [
                    {
                        "url": "https://example.test/1",
                        "title": "T" * 700,
                        "description": "D" * 4_000,
                    },
                    {
                        "url": "https://example.test/2",
                        "title": "Second",
                    },
                ]
            },
        },
        result_limit=1,
    )

    assert len(parsed) == 1
    assert len(parsed[0].title) == 500
    assert len(parsed[0].content or "") == 2_000


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"success": False, "error": "untrusted provider text"},
        {"success": True},
        {"success": True, "data": []},
        {"success": True, "data": {"web": "not-a-list"}},
    ],
    ids=["none", "array-root", "provider-error", "missing-data", "bad-data", "bad-web"],
)
def test_parser_rejects_unexpected_response_shapes_without_retaining_body(
    payload: object,
) -> None:
    with pytest.raises(FirecrawlSearchError):
        parse_firecrawl_search_response(payload)


def test_parser_drops_individual_malformed_hits() -> None:
    parsed = parse_firecrawl_search_response(
        {
            "success": True,
            "data": {
                "web": [
                    "bad entry",
                    {"url": "https://example.test/1", "title": ""},
                    {"url": "https://example.test/2", "title": "Valid"},
                ]
            },
        }
    )
    assert [(result.url, result.title) for result in parsed] == [
        ("https://example.test/2", "Valid")
    ]


def test_http_error_does_not_retain_provider_body() -> None:
    client = make_client(
        lambda request: httpx.Response(503, json={"message": "private body marker"})
    )

    with pytest.raises(FirecrawlSearchError, match="HTTP 503") as caught:
        client.search("query")
    assert "private body marker" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_invalid_json_and_transport_errors_are_generic() -> None:
    client = make_client(lambda request: httpx.Response(200, text="secret response"))
    with pytest.raises(FirecrawlSearchError, match="not valid JSON") as caught:
        client.search("query")
    assert "secret response" not in str(caught.value)

    def broken_transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private transport details", request=request)

    client = make_client(broken_transport)
    with pytest.raises(FirecrawlSearchError, match="request failed") as caught:
        client.search("query")
    assert "private transport details" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_client_requires_api_key_and_bounded_limit() -> None:
    with pytest.raises(ValueError, match="requires an API key"):
        FirecrawlSearchClient(BASE_URL, api_key=None)
    with pytest.raises(ValueError, match="supported bound"):
        FirecrawlSearchClient(BASE_URL, api_key=API_KEY, result_limit=101)


def test_discovery_factory_selects_firecrawl_search_without_making_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        search_provider="firecrawl",
        firecrawl_api_key=API_KEY,
    )
    monkeypatch.setattr(
        "app.agents.discovery.agent.get_settings", lambda: settings
    )

    agent = register_discovery_handler(
        HandlerRegistry(),
        firecrawl=FirecrawlClient("https://firecrawl.test", api_key=API_KEY),
    )

    assert isinstance(agent._search, FirecrawlSearchClient)
