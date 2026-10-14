"""Offline tests for the discovery failure diagnostic boundary."""

from __future__ import annotations

import json
import logging
from uuid import UUID

import httpx
import pytest

import app.clients.firecrawl as firecrawl_module
from app.agents.discovery.agent import DiscoveryAgent, _Candidate, _record_issue
from app.agents.discovery.classify import sanitize_public_url
from app.agents.discovery.contract import (
    DiscoveryIssue,
    DiscoveryIssueCategory,
    DiscoveryResult,
)
from app.clients.firecrawl import FirecrawlClient, FirecrawlError
from tests.fake_clients import FakeFirecrawlClient, FakeSearXNGClient, search_result

_TEST_CORRELATION_ID = "12345678123456781234567812345678"


def _log_mocked_http_error(message: str, caplog, monkeypatch, *, code=None):
    monkeypatch.setattr(
        firecrawl_module,
        "uuid4",
        lambda: UUID("12345678-1234-5678-1234-567812345678"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["External-Request-Id"] == _TEST_CORRELATION_ID
        payload = {"error": message}
        if code is not None:
            payload["code"] = code
        return httpx.Response(
            500,
            json=payload,
            headers={"X-Request-Id": "provider-req-500"},
        )

    url = "https://boards.example.com/jobs/42?access_token=private-query"
    firecrawl = FirecrawlClient(
        "http://firecrawl.test", transport=httpx.MockTransport(handler)
    )
    agent = DiscoveryAgent(FakeSearXNGClient(), firecrawl)
    candidate = _Candidate(
        result=search_result(url, "AI Engineer"),
        canonical_url="https://boards.example.com/jobs/42",
    )

    with caplog.at_level(logging.WARNING, logger="app.agents.discovery.agent"):
        outcome = agent._crawl(candidate)

    record = next(
        record
        for record in caplog.records
        if record.getMessage().startswith("Firecrawl scrape failed ")
    )
    log_message = record.getMessage()
    diagnostic = json.loads(log_message.removeprefix("Firecrawl scrape failed "))
    return outcome, diagnostic, log_message


def test_crawl_failure_logs_safe_provider_diagnostics(caplog) -> None:
    url = "https://boards.example.com/jobs/42?access_token=private-value"
    error = FirecrawlError(
        "untrusted provider details",
        status_code=500,
        kind="http_error",
        correlation_id="12345678123456781234567812345678",
        request_id="fc-request-42",
        provider_error_code="SCRAPE_ALL_ENGINES_FAILED",
        provider_message="worker unavailable",
    )
    agent = DiscoveryAgent(
        FakeSearXNGClient(),
        FakeFirecrawlClient({url: error}),
    )
    candidate = _Candidate(
        result=search_result(url, "AI Engineer"),
        canonical_url="https://boards.example.com/jobs/42",
    )

    with caplog.at_level(logging.WARNING, logger="app.agents.discovery.agent"):
        outcome = agent._crawl(candidate)

    assert outcome.category.value == "http_error"
    assert outcome.status_code == 500
    assert outcome.error == "crawl failed with HTTP 500"
    assert "private-value" not in outcome.error
    assert "jobs/42" not in outcome.error
    assert "untrusted provider details" not in outcome.error
    assert "SCRAPE_ALL_ENGINES_FAILED" not in outcome.error

    record = next(
        record
        for record in caplog.records
        if record.getMessage().startswith("Firecrawl scrape failed ")
    )
    diagnostic = json.loads(
        record.getMessage().removeprefix("Firecrawl scrape failed ")
    )
    assert diagnostic == {
        "category": "http_error",
        "correlation_id": "12345678123456781234567812345678",
        "error_kind": "http_error",
        "provider_error_code": "SCRAPE_ALL_ENGINES_FAILED",
        "provider_message": "worker unavailable",
        "provider_request_id": "fc-request-42",
        "stage": "crawl",
        "status_code": 500,
    }
    assert "private-value" not in record.getMessage()
    assert "boards.example.com" not in record.getMessage()


def test_public_discovery_output_contains_only_safe_url_and_generic_diagnostics() -> None:
    raw_url = (
        "https://user:password@boards.example.com/reset/"
        "ABCDEF0123456789_ABCDEF0123456789_ABCDEF"
        "?access_token=private-value#fragment"
    )
    issues: list[DiscoveryIssue] = []
    _record_issue(
        issues,
        raw_url,
        "crawl",
        DiscoveryIssueCategory.http_error,
        status=500,
        message="crawl failed with HTTP 500",
    )
    directly_constructed_issue = DiscoveryIssue(
        url=raw_url,
        stage="crawl",
        category=DiscoveryIssueCategory.http_error,
        status=500,
        message="crawl failed with HTTP 500",
    )
    assert directly_constructed_issue.url == "https://boards.example.com"
    result = DiscoveryResult(
        query={"role": "AI Engineer", "location": "Bengaluru", "experience": "1-3"},
        searched=1,
        candidates_found=1,
        jobs_created=0,
        jobs_updated=0,
        jobs_skipped=0,
        errors=["crawl failed with HTTP 500"],
        job_ids=[],
        issues=issues,
    ).model_dump(mode="json")

    serialized = json.dumps(result)
    assert result["issues"][0]["url"] == "https://boards.example.com"
    assert result["issues"][0]["message"] == "crawl failed with HTTP 500"
    assert "jobs/" not in serialized
    assert "ABCDEF0123456789" not in serialized
    assert "password" not in serialized
    assert "private-value" not in serialized
    assert "SCRAPE_ALL_ENGINES_FAILED" not in serialized
    assert "correlation_id" not in serialized
    assert "provider_error_code" not in serialized


@pytest.mark.parametrize(
    "unrecognized_code",
    [
        "INTERNAL_ERROR",
        "ABCDEF0123456789_ABCDEF0123456789_ABCDEF",
    ],
)
def test_unrecognized_provider_code_cannot_leak_through_message_or_logs(
    unrecognized_code: str, caplog, monkeypatch
) -> None:
    correlation_id = "12345678123456781234567812345678"
    monkeypatch.setattr(
        firecrawl_module,
        "uuid4",
        lambda: UUID("12345678-1234-5678-1234-567812345678"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["External-Request-Id"] == correlation_id
        return httpx.Response(
            500,
            json={
                "code": unrecognized_code,
                "error": unrecognized_code,
                "message": unrecognized_code,
            },
            headers={"X-Request-Id": "provider-req-500"},
        )

    url = "https://boards.example.com/jobs/42"
    firecrawl = FirecrawlClient(
        "http://firecrawl.test", transport=httpx.MockTransport(handler)
    )
    agent = DiscoveryAgent(FakeSearXNGClient(), firecrawl)
    candidate = _Candidate(
        result=search_result(url, "AI Engineer"),
        canonical_url=url,
    )

    with caplog.at_level(logging.WARNING, logger="app.agents.discovery.agent"):
        outcome = agent._crawl(candidate)

    record = next(
        record
        for record in caplog.records
        if record.getMessage().startswith("Firecrawl scrape failed ")
    )
    log_message = record.getMessage()
    diagnostic = json.loads(log_message.removeprefix("Firecrawl scrape failed "))
    assert unrecognized_code not in log_message
    assert diagnostic["provider_error_code"] is None
    assert diagnostic["provider_message"] is None
    assert diagnostic["status_code"] == 500
    assert diagnostic["error_kind"] == "http_error"
    assert diagnostic["correlation_id"] == correlation_id
    assert diagnostic["provider_request_id"] == "provider-req-500"
    assert outcome.error == "crawl failed with HTTP 500"

    issues: list[DiscoveryIssue] = []
    _record_issue(
        issues,
        url,
        "crawl",
        outcome.category,
        status=outcome.status_code,
        message=outcome.error,
    )
    public_output = DiscoveryResult(
        query={"role": "AI Engineer", "location": "Bengaluru", "experience": "1-3"},
        searched=1,
        candidates_found=1,
        jobs_created=0,
        jobs_updated=0,
        jobs_skipped=0,
        errors=[outcome.error],
        job_ids=[],
        issues=issues,
    ).model_dump(mode="json")
    serialized_output = json.dumps(public_output)
    assert unrecognized_code not in serialized_output
    assert "crawl failed with HTTP 500" in serialized_output


@pytest.mark.parametrize(
    ("provider_message", "secret_markers"),
    [
        (
            "provider rejected /reset/short-secret#fragment",
            ("/reset/short-secret", "short-secret", "#fragment"),
        ),
        (
            "provider failed at ../account/private-api-key",
            ("../account/private-api-key", "private-api-key"),
        ),
        (
            "failure at /jobs/secret-id?access_token=query-secret#fragment",
            ("/jobs/secret-id", "secret-id", "query-secret", "#fragment"),
        ),
        (
            "provider failed at ./reset%2Fencoded-secret%23encoded-fragment",
            ("./reset", "encoded-secret", "encoded-fragment"),
        ),
        (
            "provider failed at /reset&#x2f;html-encoded-secret",
            ("html-encoded-secret", "&#x2f;"),
        ),
        (
            "provider failed at /reset／unicode-secret",
            ("unicode-secret", "／"),
        ),
    ],
)
def test_path_or_fragment_provider_message_is_rejected_before_discovery_log(
    provider_message: str, secret_markers: tuple[str, ...], caplog, monkeypatch
) -> None:
    outcome, diagnostic, log_message = _log_mocked_http_error(
        provider_message, caplog, monkeypatch
    )

    assert diagnostic["provider_message"] is None
    assert diagnostic["status_code"] == 500
    assert diagnostic["error_kind"] == "http_error"
    assert diagnostic["correlation_id"] == _TEST_CORRELATION_ID
    assert diagnostic["provider_request_id"] == "provider-req-500"
    assert outcome.error == "crawl failed with HTTP 500"
    for marker in secret_markers:
        assert marker not in log_message


def test_safe_provider_message_remains_useful_in_discovery_log(caplog, monkeypatch):
    outcome, diagnostic, log_message = _log_mocked_http_error(
        "worker temporarily unavailable", caplog, monkeypatch
    )

    assert diagnostic["provider_message"] == "worker temporarily unavailable"
    assert diagnostic["status_code"] == 500
    assert diagnostic["error_kind"] == "http_error"
    assert diagnostic["correlation_id"] == _TEST_CORRELATION_ID
    assert diagnostic["provider_request_id"] == "provider-req-500"
    assert "worker temporarily unavailable" in log_message
    assert outcome.error == "crawl failed with HTTP 500"


def test_rejected_code_inside_provider_message_does_not_reach_log(
    caplog, monkeypatch
) -> None:
    outcome, diagnostic, log_message = _log_mocked_http_error(
        "worker failed with INTERNAL_ERROR", caplog, monkeypatch, code="INTERNAL_ERROR"
    )

    assert diagnostic["provider_error_code"] is None
    assert diagnostic["provider_message"] is None
    assert "INTERNAL_ERROR" not in log_message
    assert diagnostic["status_code"] == 500
    assert diagnostic["error_kind"] == "http_error"
    assert diagnostic["correlation_id"] == _TEST_CORRELATION_ID
    assert diagnostic["provider_request_id"] == "provider-req-500"
    assert outcome.error == "crawl failed with HTTP 500"
