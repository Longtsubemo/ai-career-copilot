"""Discovery quality: URL listing skips, diagnostics and availability.

Database-backed, but external services are the scripted fakes; no test here
opens a socket.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from app.agents.discovery import (
    MAX_DISCOVERY_ISSUES,
    DiscoveryAgent,
    DiscoveryRequest,
)
from app.agents.discovery.contract import DiscoveryIssueCategory
from app.clients.firecrawl import FirecrawlError
from app.models.enums import AvailabilityStatus, CrawlStatus
from app.models.job import Job
from app.services.normalization import canonicalize_url
from tests.fake_clients import (
    FakeFirecrawlClient,
    FakeSearXNGClient,
    crawl_result,
    listing_page,
    search_result,
)

T1 = datetime(2026, 1, 15, tzinfo=timezone.utc)
T2 = datetime(2026, 2, 15, tzinfo=timezone.utc)

URL_A = "https://boards.example.com/jobs/42"
REQUEST = DiscoveryRequest(
    role="AI Engineer", location="Bangalore", experience="1-3 years"
)


def make_agent(results=(), pages=None, *, clock=None) -> DiscoveryAgent:
    return DiscoveryAgent(
        FakeSearXNGClient(results),
        FakeFirecrawlClient(pages),
        clock=clock or (lambda: T1),
    )


def get_job(session, url: str) -> Job:
    job = session.scalar(
        select(Job).where(Job.canonical_url == canonicalize_url(url))
    )
    assert job is not None
    return job


def issue_for(output: dict, category: str) -> dict:
    matches = [issue for issue in output["issues"] if issue["category"] == category]
    assert matches, f"no {category!r} issue in {output['issues']!r}"
    return matches[0]


def assert_invariant(output: dict) -> None:
    assert (
        output["jobs_created"]
        + output["jobs_updated"]
        + output["jobs_skipped"]
        + output["listings_skipped"]
        + output["invalid_results"]
        == output["candidates_found"]
    )
    assert (
        output["extractions_succeeded"] + output["scrape_failures"]
        == output["scrape_attempts"]
    )


def open_page(url: str) -> object:
    return listing_page(url, title="AI Engineer", location="Bangalore")


class TestListingsAndDiagnostics:
    def test_listing_url_is_skipped_before_any_crawl(self, session) -> None:
        agent = make_agent(
            [search_result("https://example.com/jobs", "AI Engineer")], {}
        )

        output = agent.discover(session, REQUEST)

        assert output["candidates_found"] == 1
        assert output["listings_skipped"] == 1
        assert output["scrape_attempts"] == 0
        assert output["jobs_created"] == 0
        assert issue_for(output, "listing_page")["stage"] == "classify"
        assert_invariant(output)

    def test_page_listing_reclassification_skips_after_crawl(self, session) -> None:
        url = "https://example.com/55"
        pages = {
            url: crawl_result(
                url,
                markdown="# Browse all jobs\n\nAI Engineer openings.",
                metadata={"title": "AI Engineer", "location": "Bangalore"},
            )
        }
        agent = make_agent([search_result(url, "AI Engineer")], pages)

        output = agent.discover(session, REQUEST)

        assert output["listings_skipped"] == 1
        assert output["scrape_attempts"] == 1
        assert output["jobs_created"] == 0
        assert output["extractions_succeeded"] == 1
        assert_invariant(output)

    def test_usable_crawl_rejected_by_filter_still_counts_as_extraction(
        self, session
    ) -> None:
        page = listing_page(URL_A, title="Data Scientist", location="Bangalore")
        agent = make_agent([search_result(URL_A, "Data Scientist")], {URL_A: page})

        output = agent.discover(session, REQUEST)

        assert output["jobs_skipped"] == 1
        assert output["jobs_created"] == 0
        assert output["extractions_succeeded"] == 1
        assert output["scrape_failures"] == 0
        assert_invariant(output)

    def test_malformed_results_are_counted_and_issue_list_is_capped(
        self, session
    ) -> None:
        results = [search_result("javascript:void(0)", "AI Engineer")] * 30
        agent = make_agent(results, {})

        output = agent.discover(session, REQUEST)

        assert output["malformed_results"] == 60
        assert output["candidates_found"] == 0
        assert len(output["issues"]) == MAX_DISCOVERY_ISSUES
        assert_invariant(output)

    def test_duplicate_urls_are_counted(self, session) -> None:
        results = [
            search_result(f"{URL_A}?utm_source=x", "AI Engineer"),
            search_result(f"{URL_A}/", "AI Engineer"),
        ]
        agent = make_agent(results, {URL_A: open_page(URL_A)})

        output = agent.discover(session, REQUEST)

        assert output["duplicates_skipped"] == 3
        assert output["candidates_found"] == 1
        assert output["jobs_created"] == 1
        assert_invariant(output)

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (FirecrawlError("forbidden", status_code=403, kind="http_error"), "access_denied"),
            (FirecrawlError("gone", status_code=410, kind="http_error"), "http_error"),
            (FirecrawlError("slow", kind="timeout"), "timeout"),
            (FirecrawlError("down", kind="network_error"), "network_error"),
            (FirecrawlError("bad", kind="api_error"), "scrape_failure"),
        ],
    )
    def test_scrape_failure_categories(self, session, error, expected) -> None:
        agent = make_agent([search_result(URL_A, "AI Engineer")], {URL_A: error})

        output = agent.discover(session, REQUEST)

        assert output["scrape_failures"] == 1
        assert issue_for(output, expected)["stage"] == "crawl"
        assert_invariant(output)

    def test_empty_content_is_empty_extraction(self, session) -> None:
        page = crawl_result(URL_A, markdown=None, metadata={})
        agent = make_agent([search_result(URL_A, "AI Engineer")], {URL_A: page})

        output = agent.discover(session, REQUEST)

        assert output["scrape_failures"] == 1
        assert issue_for(output, "empty_extraction")
        assert output["extractions_succeeded"] == 0
        assert get_job(session, URL_A).crawl_status is CrawlStatus.failed
        assert_invariant(output)


class TestAvailability:
    def test_successful_crawl_marks_available(self, session) -> None:
        agent = make_agent([search_result(URL_A, "AI Engineer")], {URL_A: open_page(URL_A)})

        output = agent.discover(session, REQUEST)

        job = get_job(session, URL_A)
        assert job.availability is AvailabilityStatus.available
        assert job.crawl_status is CrawlStatus.scraped
        assert job.crawled_at == T1
        assert job.closed_at is None
        assert output["extractions_succeeded"] == 1
        assert output["scrape_attempts"] == 1
        assert output["scrape_failures"] == 0

    def test_closure_evidence_marks_closed(self, session) -> None:
        page = crawl_result(
            URL_A,
            markdown="# AI Engineer\n\nThis position has been filled.",
            metadata={"title": "AI Engineer", "location": "Bangalore"},
        )
        agent = make_agent([search_result(URL_A, "AI Engineer")], {URL_A: page})

        agent.discover(session, REQUEST)

        job = get_job(session, URL_A)
        assert job.availability is AvailabilityStatus.closed
        assert job.closed_at == T1
        assert job.crawl_status is CrawlStatus.scraped

    def test_failed_recrawl_preserves_availability(self, session) -> None:
        first = make_agent(
            [search_result(URL_A, "AI Engineer")],
            {URL_A: open_page(URL_A)},
            clock=lambda: T1,
        )
        first.discover(session, REQUEST)

        second = make_agent(
            [search_result(URL_A, "AI Engineer")],
            {URL_A: FirecrawlError("boom", kind="network_error")},
            clock=lambda: T2,
        )
        second.discover(session, REQUEST)
        session.expire_all()

        job = get_job(session, URL_A)
        assert job.availability is AvailabilityStatus.available
        assert job.closed_at is None
        assert job.crawl_status is CrawlStatus.failed
        assert job.crawled_at == T1

    def test_new_job_from_failed_crawl_stays_unknown(self, session) -> None:
        agent = make_agent(
            [search_result(URL_A, "AI Engineer")],
            {URL_A: FirecrawlError("boom", kind="network_error")},
        )

        agent.discover(session, REQUEST)

        assert get_job(session, URL_A).availability is AvailabilityStatus.unknown


class TestDatePosted:
    def test_iso_date_posted_metadata_is_persisted(self, session) -> None:
        page = listing_page(
            URL_A, title="AI Engineer", location="Bangalore"
        )
        page.metadata["datePosted"] = "2026-01-05"
        agent = make_agent([search_result(URL_A, "AI Engineer")], {URL_A: page})

        agent.discover(session, REQUEST)

        assert get_job(session, URL_A).date_posted == date(2026, 1, 5)

    def test_vague_date_posted_is_ignored(self, session) -> None:
        page = listing_page(URL_A, title="AI Engineer", location="Bangalore")
        page.metadata["datePosted"] = "posted recently"
        agent = make_agent([search_result(URL_A, "AI Engineer")], {URL_A: page})

        agent.discover(session, REQUEST)

        assert get_job(session, URL_A).date_posted is None
