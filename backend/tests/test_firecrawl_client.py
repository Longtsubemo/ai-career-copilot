"""Behaviour of the Firecrawl client and its response parsing.

Transport is exercised through ``httpx.MockTransport``; no test opens a
real socket.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.clients.firecrawl import (
    DEFAULT_SCRAPE_PATH,
    FirecrawlClient,
    FirecrawlError,
    parse_scrape_response,
)

BASE_URL = "http://firecrawl.test"

VALID_BODY = {
    "success": True,
    "data": {
        "markdown": "# Senior AI Engineer",
        "metadata": {"title": "Senior AI Engineer"},
    },
}


def test_parse_scrape_response_reads_markdown_and_metadata() -> None:
    result = parse_scrape_response(VALID_BODY, requested_url="https://example.com/j/1")

    assert result.url == "https://example.com/j/1"
    assert result.markdown == "# Senior AI Engineer"
    assert result.metadata == {"title": "Senior AI Engineer"}


@pytest.mark.parametrize("fallback_key", ["markdown", "content", "text"])
def test_parse_scrape_response_accepts_every_content_field(
    fallback_key: str,
) -> None:
    body = {
        "success": True,
        "data": {fallback_key: "page body", "metadata": {}},
    }

    result = parse_scrape_response(body, requested_url="https://example.com")

    assert result.markdown == "page body"


def test_parse_scrape_response_defaults_metadata_and_missing_content() -> None:
    result = parse_scrape_response(
        {"success": True, "data": {}}, requested_url="https://example.com"
    )

    assert result.markdown is None
    assert result.metadata == {}


def test_parse_scrape_response_keeps_non_string_metadata_values() -> None:
    body = {"success": True, "data": {"markdown": "x", "metadata": {"score": 0.5}}}

    result = parse_scrape_response(body, requested_url="https://example.com")

    assert result.metadata == {"score": 0.5}


def test_parse_scrape_response_surfaces_the_api_error_message() -> None:
    body = {"success": False, "error": "URL returned 404"}

    with pytest.raises(FirecrawlError, match="URL returned 404"):
        parse_scrape_response(body, requested_url="https://example.com")


def test_parse_scrape_response_rejects_bad_shapes() -> None:
    with pytest.raises(FirecrawlError, match="not a JSON object"):
        parse_scrape_response(["list"], requested_url="https://example.com")
    with pytest.raises(FirecrawlError, match="success=false"):
        parse_scrape_response({}, requested_url="https://example.com")
    with pytest.raises(FirecrawlError, match="no 'data' object"):
        parse_scrape_response(
            {"success": True, "data": "not an object"},
            requested_url="https://example.com",
        )


def make_client(handler, **kwargs: object) -> FirecrawlClient:
    return FirecrawlClient(
        BASE_URL, transport=httpx.MockTransport(handler), **kwargs  # type: ignore[arg-type]
    )


def test_scrape_posts_the_documented_body() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json=VALID_BODY)

    client = make_client(handler)
    result = client.scrape("https://example.com/j/1")

    assert result.markdown == "# Senior AI Engineer"
    assert seen["method"] == "POST"
    assert seen["path"] == DEFAULT_SCRAPE_PATH
    assert seen["body"] == {
        "url": "https://example.com/j/1",
        "formats": ["markdown"],
    }
    assert seen["authorization"] is None


def test_scrape_sends_the_bearer_token_when_an_api_key_is_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-key"
        return httpx.Response(200, json=VALID_BODY)

    client = make_client(handler, api_key="secret-key")

    client.scrape("https://example.com/j/1")


def test_scrape_honours_a_custom_scrape_path_and_base_slash() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=VALID_BODY)

    client = FirecrawlClient(
        f"{BASE_URL}/",
        scrape_path="/custom/scrape",
        transport=httpx.MockTransport(handler),
    )
    client.scrape("https://example.com")

    assert seen["path"] == "/custom/scrape"


def test_scrape_maps_http_error_status_to_firecrawl_error() -> None:
    client = make_client(lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(FirecrawlError, match="HTTP 500"):
        client.scrape("https://example.com")


def test_scrape_maps_invalid_json_to_firecrawl_error() -> None:
    client = make_client(lambda request: httpx.Response(200, text="<html>"))

    with pytest.raises(FirecrawlError, match="not valid JSON"):
        client.scrape("https://example.com")


def test_scrape_maps_success_false_to_firecrawl_error() -> None:
    client = make_client(
        lambda request: httpx.Response(200, json={"success": False, "error": "nope"})
    )

    with pytest.raises(FirecrawlError, match="nope"):
        client.scrape("https://example.com")


def test_scrape_maps_transport_failure_to_firecrawl_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = make_client(handler)

    with pytest.raises(FirecrawlError, match="request failed"):
        client.scrape("https://example.com")


def test_client_validates_its_configuration() -> None:
    with pytest.raises(ValueError):
        FirecrawlClient("")
    with pytest.raises(ValueError):
        FirecrawlClient(BASE_URL, scrape_path="v2/scrape")
