"""Behaviour of the Firecrawl client and its response parsing.

Transport is exercised through ``httpx.MockTransport``; no test opens a
real socket.
"""

from __future__ import annotations

import json
from uuid import UUID

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

    with pytest.raises(FirecrawlError, match="scrape failed") as caught:
        parse_scrape_response(body, requested_url="https://example.com")
    assert caught.value.kind == "api_error"
    assert caught.value.provider_message == "URL returned 404"


def test_parse_scrape_response_rejects_bad_shapes() -> None:
    with pytest.raises(FirecrawlError, match="not a JSON object") as caught:
        parse_scrape_response(["list"], requested_url="https://example.com")
    assert caught.value.kind == "shape_error"
    with pytest.raises(FirecrawlError, match="scrape failed") as caught:
        parse_scrape_response({}, requested_url="https://example.com")
    assert caught.value.kind == "api_error"
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
        seen["external_request_id"] = request.headers.get("External-Request-Id")
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
    assert isinstance(seen.get("external_request_id"), str)
    assert len(seen["external_request_id"]) == 32


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


def test_scrape_maps_http_500_with_safe_provider_diagnostics() -> None:
    client = make_client(
        lambda request: httpx.Response(
            500,
            json={
                "error": {
                    "code": "SCRAPE_ALL_ENGINES_FAILED",
                    "message": "worker unavailable",
                }
            },
            headers={"X-Firecrawl-Request-Id": "fc-req-1234"},
        )
    )

    with pytest.raises(FirecrawlError, match="HTTP 500") as caught:
        client.scrape("https://example.com")

    error = caught.value
    assert error.kind == "http_error"
    assert error.status_code == 500
    assert error.request_id == "fc-req-1234"
    assert error.provider_error_code == "SCRAPE_ALL_ENGINES_FAILED"
    assert error.provider_message == "worker unavailable"
    assert "worker unavailable" not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_http_500_controller_shape_sends_and_retains_application_correlation_id(
    monkeypatch,
) -> None:
    import app.clients.firecrawl as firecrawl_module

    expected_id = "12345678123456781234567812345678"
    monkeypatch.setattr(
        firecrawl_module,
        "uuid4",
        lambda: UUID("12345678-1234-5678-1234-567812345678"),
    )
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["external_request_id"] = request.headers.get("External-Request-Id")
        return httpx.Response(
            500,
            json={
                "success": False,
                "code": "SCRAPE_ALL_ENGINES_FAILED",
                "error": "All scraping engines failed. Engines tried: [pdf, document].",
            },
        )

    client = make_client(handler)
    with pytest.raises(FirecrawlError, match="HTTP 500") as caught:
        client.scrape("https://example.com/jobs/42")

    assert seen["external_request_id"] == expected_id
    assert caught.value.correlation_id == expected_id
    assert caught.value.request_id is None
    assert caught.value.status_code == 500
    assert caught.value.kind == "http_error"
    assert caught.value.provider_error_code == "SCRAPE_ALL_ENGINES_FAILED"
    assert caught.value.provider_message == (
        "All scraping engines failed. Engines tried: [pdf, document]."
    )
    assert "All scraping engines failed" not in str(caught.value)


def test_http_error_without_structured_fields_retains_only_app_correlation(
    monkeypatch,
) -> None:
    import app.clients.firecrawl as firecrawl_module

    expected_id = "abcdefabcdefabcdefabcdefabcdefab"
    monkeypatch.setattr(
        firecrawl_module,
        "uuid4",
        lambda: UUID("abcdefab-cdef-abcd-efab-cdefabcdefab"),
    )
    client = make_client(lambda request: httpx.Response(500, json={"success": False}))

    with pytest.raises(FirecrawlError) as caught:
        client.scrape("https://example.com")

    assert caught.value.correlation_id == expected_id
    assert caught.value.request_id is None
    assert caught.value.provider_error_code is None
    assert caught.value.provider_message is None


@pytest.mark.parametrize("body", [b"", b"<html>upstream error</html>", b"not json"])
def test_http_500_without_json_diagnostics_keeps_status(body: bytes) -> None:
    client = make_client(lambda request: httpx.Response(500, content=body))

    with pytest.raises(FirecrawlError, match="HTTP 500") as caught:
        client.scrape("https://example.com")

    assert caught.value.kind == "http_error"
    assert caught.value.status_code == 500
    assert caught.value.request_id is None
    assert caught.value.provider_error_code is None
    assert caught.value.provider_message is None


def test_http_error_drops_oversized_provider_message() -> None:
    client = make_client(
        lambda request: httpx.Response(
            500,
            json={"code": "SCRAPE_ALL_ENGINES_FAILED", "message": "x" * 1025},
            headers={"X-Request-Id": "req-oversized"},
        )
    )

    with pytest.raises(FirecrawlError) as caught:
        client.scrape("https://example.com")

    assert caught.value.request_id == "req-oversized"
    assert caught.value.provider_error_code == "SCRAPE_ALL_ENGINES_FAILED"
    assert caught.value.provider_message is None


def test_http_error_drops_diagnostics_when_entire_body_is_oversized() -> None:
    client = make_client(
        lambda request: httpx.Response(
            500,
            json={"code": "INTERNAL_ERROR", "message": "x" * (20 * 1024)},
        )
    )

    with pytest.raises(FirecrawlError) as caught:
        client.scrape("https://example.com")

    assert caught.value.status_code == 500
    assert caught.value.kind == "http_error"
    assert caught.value.provider_error_code is None
    assert caught.value.provider_message is None


def test_http_error_redacts_sensitive_provider_message() -> None:
    unsafe_message = (
        "fetch failed https://provider.test/job?access_token=secret-value "
        "Authorization: Bearer bearer-secret token=another-secret "
        "Cookie: session-id=cookie-secret; tracking=private-cookie"
    )
    client = make_client(
        lambda request: httpx.Response(
            500,
            json={"message": unsafe_message},
            headers={"X-Request-Id": "req-redacted"},
        )
    )

    with pytest.raises(FirecrawlError) as caught:
        client.scrape("https://example.com")

    safe_message = caught.value.provider_message
    assert safe_message is not None
    for secret in (
        "secret-value",
        "bearer-secret",
        "another-secret",
        "cookie-secret",
        "private-cookie",
    ):
        assert secret not in safe_message
    assert "[url]" in safe_message


def test_http_error_ignores_unallowlisted_request_id_and_provider_code() -> None:
    client = make_client(
        lambda request: httpx.Response(
            500,
            json={"code": "INTERNAL"},
            headers={"X-Private-Token": "should-not-be-used"},
        )
    )

    with pytest.raises(FirecrawlError) as caught:
        client.scrape("https://example.com")

    assert caught.value.request_id is None
    assert caught.value.provider_error_code is None


@pytest.mark.parametrize(
    "code",
    [
        "a" * 32,
        "ABCDEF0123456789_ABCDEF0123456789_ABCDEF",
        "UNKNOWN_PROVIDER_ERROR",
        "X" * 65,
        "SCRAPE_ALL_ENGINES_FAILED!",
    ],
)
def test_http_error_rejects_unrecognized_or_unsafe_provider_codes(code: str) -> None:
    client = make_client(lambda request: httpx.Response(500, json={"code": code}))

    with pytest.raises(FirecrawlError) as caught:
        client.scrape("https://example.com")

    assert caught.value.provider_error_code is None
    assert caught.value.status_code == 500
    assert caught.value.kind == "http_error"


def test_http_diagnostic_parse_failure_does_not_mask_status(monkeypatch) -> None:
    import app.clients.firecrawl as firecrawl_module

    def fail_diagnostic_parse(payload: object) -> tuple[str | None, str | None]:
        raise RuntimeError("diagnostic parser failed")

    monkeypatch.setattr(firecrawl_module, "_provider_error_details", fail_diagnostic_parse)
    client = make_client(lambda request: httpx.Response(500, json={"message": "error"}))

    with pytest.raises(FirecrawlError, match="HTTP 500") as caught:
        client.scrape("https://example.com")

    assert caught.value.kind == "http_error"
    assert caught.value.status_code == 500
    assert caught.value.provider_message is None


def test_scrape_maps_invalid_json_to_firecrawl_error() -> None:
    client = make_client(lambda request: httpx.Response(200, text="<html>"))

    with pytest.raises(FirecrawlError, match="not valid JSON") as caught:
        client.scrape("https://example.com")
    assert caught.value.kind == "shape_error"


def test_scrape_maps_success_false_to_firecrawl_error() -> None:
    client = make_client(
        lambda request: httpx.Response(
            200,
            json={"success": False, "error": "nope"},
            headers={"X-Request-Id": "api-req-1"},
        )
    )

    with pytest.raises(FirecrawlError, match="scrape failed") as caught:
        client.scrape("https://example.com")
    assert caught.value.kind == "api_error"
    assert caught.value.provider_message == "nope"
    assert caught.value.request_id == "api-req-1"


def test_scrape_maps_invalid_response_shape_to_shape_error() -> None:
    client = make_client(lambda request: httpx.Response(200, json={"success": True}))

    with pytest.raises(FirecrawlError, match="no 'data' object") as caught:
        client.scrape("https://example.com")

    assert caught.value.kind == "shape_error"


def test_scrape_maps_timeout_to_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = make_client(handler)

    with pytest.raises(FirecrawlError, match="timed out") as caught:
        client.scrape("https://example.com")

    assert caught.value.kind == "timeout"


def test_scrape_maps_transport_failure_to_firecrawl_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = make_client(handler)

    with pytest.raises(FirecrawlError, match="request failed") as caught:
        client.scrape("https://example.com")
    assert caught.value.kind == "network_error"


def test_client_validates_its_configuration() -> None:
    with pytest.raises(ValueError):
        FirecrawlClient("")
    with pytest.raises(ValueError):
        FirecrawlClient(BASE_URL, scrape_path="v2/scrape")
