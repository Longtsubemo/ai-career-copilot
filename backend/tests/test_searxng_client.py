"""Behaviour of the SearXNG client and its response parsing.

Transport is exercised through ``httpx.MockTransport``; no test opens a
real socket.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.clients.searxng import (
    SearXNGClient,
    SearXNGError,
    build_search_url,
    parse_search_response,
)

BASE_URL = "http://searxng.test"

VALID_BODY = {
    "results": [
        {"url": "https://example.com/a", "title": "AI Engineer", "content": "Build agents."},
        {"url": "https://example.com/b", "title": "ML Engineer"},
        {"url": "https://example.com/c", "title": "", "content": "dropped: empty title"},
        {"url": "", "title": "Dropped", "content": "dropped: empty url"},
        {"url": 42, "title": "Dropped"},
        "not-a-mapping",
    ]
}


def test_build_search_url_places_query_params() -> None:
    url = build_search_url(BASE_URL, "AI Engineer Bangalore jobs 1-3 years")

    parsed = httpx.URL(url)
    assert parsed.path == "/search"
    assert parsed.params["q"] == "AI Engineer Bangalore jobs 1-3 years"
    assert parsed.params["format"] == "json"
    assert parsed.params["pageno"] == "1"


def test_build_search_url_strips_base_trailing_slash_and_honours_page() -> None:
    url = build_search_url("http://searxng.test/", "backend", page=3)

    parsed = httpx.URL(url)
    assert parsed.path == "/search"
    assert parsed.params["pageno"] == "3"


def test_build_search_url_rejects_bad_arguments() -> None:
    with pytest.raises(ValueError):
        build_search_url("", "query")
    with pytest.raises(ValueError):
        build_search_url(BASE_URL, "query", page=0)


def test_parse_search_response_returns_normalized_results() -> None:
    parsed = parse_search_response(VALID_BODY)

    assert [(r.url, r.title) for r in parsed] == [
        ("https://example.com/a", "AI Engineer"),
        ("https://example.com/b", "ML Engineer"),
    ]
    assert parsed[0].content == "Build agents."
    assert parsed[1].content is None
    assert all(r.engine is None for r in parsed)


def test_parse_search_response_strips_and_reads_engine() -> None:
    parsed = parse_search_response(
        {
            "results": [
                {
                    "url": "  https://example.com/a  ",
                    "title": "  AI Engineer  ",
                    "content": "  snippet  ",
                    "engine": "  google  ",
                }
            ]
        }
    )

    assert parsed[0].url == "https://example.com/a"
    assert parsed[0].title == "AI Engineer"
    assert parsed[0].content == "snippet"
    assert parsed[0].engine == "google"


@pytest.mark.parametrize(
    "payload",
    [
        ["a", "list"],
        "a string",
        None,
        7,
        {"no_results_key": []},
        {"results": "a string, not a list"},
    ],
    ids=["list-root", "str-root", "none", "int", "missing-key", "string-results"],
)
def test_parse_search_response_rejects_bad_containers(payload: object) -> None:
    with pytest.raises(SearXNGError):
        parse_search_response(payload)


def make_client(handler) -> SearXNGClient:
    return SearXNGClient(BASE_URL, transport=httpx.MockTransport(handler))


def test_search_returns_results_and_builds_the_right_request() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["accept"] = request.headers.get("Accept")
        return httpx.Response(200, json=VALID_BODY)

    client = make_client(handler)
    results = client.search("ai engineer jobs", page=2)

    assert len(results) == 2
    assert seen["path"] == "/search"
    assert seen["params"]["q"] == "ai engineer jobs"
    assert seen["params"]["pageno"] == "2"
    assert seen["accept"] == "application/json"


def test_search_maps_http_error_status_to_searxng_error() -> None:
    client = make_client(lambda request: httpx.Response(503, text="down"))

    with pytest.raises(SearXNGError, match="HTTP 503"):
        client.search("query")


def test_search_maps_invalid_json_to_searxng_error() -> None:
    client = make_client(lambda request: httpx.Response(200, text="<html>oops</html>"))

    with pytest.raises(SearXNGError, match="not valid JSON"):
        client.search("query")


def test_search_maps_bad_shape_to_searxng_error() -> None:
    client = make_client(lambda request: httpx.Response(200, json={"other": 1}))

    with pytest.raises(SearXNGError, match="results"):
        client.search("query")


def test_search_maps_transport_failure_to_searxng_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler)

    with pytest.raises(SearXNGError, match="request failed"):
        client.search("query")


def test_client_rejects_an_empty_base_url() -> None:
    with pytest.raises(ValueError):
        SearXNGClient("")


def test_search_response_json_examples_round_trip() -> None:
    payload = json.loads(json.dumps(VALID_BODY))
    assert len(parse_search_response(payload)) == 2
