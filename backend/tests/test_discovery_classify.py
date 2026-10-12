"""Pure URL/page quality classification for discovery.

Everything here is deterministic and offline: no client, no database.
"""

from __future__ import annotations

import pytest

from app.agents.discovery.classify import (
    UrlQuality,
    classify_page,
    classify_url,
    crawl_http_status,
    has_closure_evidence,
    resolve_quality,
    sanitize_url,
    url_host,
)
from tests.fake_clients import crawl_result


class TestClassifyUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://boards.example.com/jobs/42",
            "https://jobs.example.com/role/99",
            "https://example.com/jobs/ai-engineer-bangalore",
            "https://www.linkedin.com/jobs/view/1234567890",
            "https://example.com/careers/12345",
            "https://example.com/apply?gh_jid=98765",
            "https://example.com/job-details/senior-ai-engineer-42",
        ],
    )
    def test_individual_vacancy_patterns(self, url: str) -> None:
        assert classify_url(url) is UrlQuality.individual

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/jobs",
            "https://example.com/jobs/search",
            "https://example.com/jobs?query=engineer",
            "https://example.com/careers",
            "https://example.com/search/results?q=ai",
            "https://example.com/category/engineering",
            "https://example.com/company/acme/jobs",
        ],
    )
    def test_listing_patterns(self, url: str) -> None:
        assert classify_url(url) is UrlQuality.listing

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/careers/engineering",
            "https://example.com/about",
            "https://example.com/0",
            "javascript:void(0)",
            "not a url",
        ],
    )
    def test_unknown_patterns(self, url: str) -> None:
        assert classify_url(url) is UrlQuality.unknown

    def test_individual_beats_a_listing_query_param(self) -> None:
        assert (
            classify_url("https://example.com/jobs/42?query=anything")
            is UrlQuality.individual
        )


class TestClassifyPage:
    def test_jobposting_metadata_marks_individual(self) -> None:
        crawl = crawl_result("https://example.com/x", metadata={"@type": "JobPosting"})
        assert classify_page(crawl) is UrlQuality.individual

    def test_structured_hiring_metadata_marks_individual(self) -> None:
        crawl = crawl_result(
            "https://example.com/x",
            metadata={"datePosted": "2026-01-05", "hiringOrganization": "Acme"},
        )
        assert classify_page(crawl) is UrlQuality.individual

    def test_listing_text_marks_listing(self) -> None:
        crawl = crawl_result(
            "https://example.com/x",
            markdown="# Browse all jobs\n\nNothing specific here.",
        )
        assert classify_page(crawl) is UrlQuality.listing

    def test_no_evidence_is_unknown(self) -> None:
        assert classify_page(crawl_result("https://example.com/x")) is UrlQuality.unknown
        assert classify_page(None) is UrlQuality.unknown


class TestResolveQuality:
    def test_individual_evidence_wins(self) -> None:
        assert (
            resolve_quality(UrlQuality.unknown, UrlQuality.individual)
            is UrlQuality.individual
        )
        assert (
            resolve_quality(UrlQuality.listing, UrlQuality.individual)
            is UrlQuality.individual
        )

    def test_listing_evidence_is_used_when_no_individual(self) -> None:
        assert (
            resolve_quality(UrlQuality.unknown, UrlQuality.listing)
            is UrlQuality.listing
        )

    def test_two_unknowns_stay_unknown(self) -> None:
        assert (
            resolve_quality(UrlQuality.unknown, UrlQuality.unknown)
            is UrlQuality.unknown
        )


class TestClosureEvidence:
    def test_http_410_is_closure(self) -> None:
        crawl = crawl_result("https://example.com/x", metadata={"statusCode": 410})
        assert has_closure_evidence(crawl) is True

    @pytest.mark.parametrize(
        "phrase",
        [
            "This position has been filled.",
            "We are no longer accepting applications.",
            "This job has closed.",
        ],
    )
    def test_closure_phrases_are_detected(self, phrase: str) -> None:
        crawl = crawl_result("https://example.com/x", markdown=f"# Role\n\n{phrase}")
        assert has_closure_evidence(crawl) is True

    def test_open_page_has_no_closure_evidence(self) -> None:
        crawl = crawl_result(
            "https://example.com/x",
            markdown="# Senior AI Engineer\n\nApply now.",
            metadata={"statusCode": 200},
        )
        assert has_closure_evidence(crawl) is False

    def test_none_crawl_has_no_closure_evidence(self) -> None:
        assert has_closure_evidence(None) is False

    def test_http_status_parses_string_and_int(self) -> None:
        assert crawl_http_status(
            crawl_result("https://example.com/x", metadata={"statusCode": "404"})
        ) == 404
        assert crawl_http_status(
            crawl_result("https://example.com/x", metadata={"status": 500})
        ) == 500
        assert crawl_http_status(crawl_result("https://example.com/x")) is None


class TestSanitizeUrl:
    def test_query_and_fragment_are_stripped(self) -> None:
        cleaned = sanitize_url("https://EXAMPLE.com/jobs/42?utm_source=x#apply")
        assert cleaned == "https://example.com/jobs/42"

    def test_long_url_is_bounded(self) -> None:
        cleaned = sanitize_url("https://example.com/jobs/" + "a" * 1000)
        assert len(cleaned) <= 300

    def test_host_extraction(self) -> None:
        assert url_host("https://Jobs.Example.com:443/role/1") == "jobs.example.com"
