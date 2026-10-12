"""Deterministic behaviour of single-job availability re-verification.

These tests drive :func:`verify_job_availability` against a real session with
a scripted Firecrawl client and a fake clock, so every evidence rule — a
credible individual page sets ``available``, affirmative closure sets
``closed``, and a timeout/404/5xx/access-denial/empty/ambiguous page preserves
the previous state — is checked without ever opening a socket.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.clients.firecrawl import FirecrawlError
from app.models.enums import AvailabilityStatus, CrawlStatus
from app.services.job_verification import (
    VERIFICATION_COOLDOWN_SECONDS,
    JobVerificationCooldownError,
    VerificationOutcome,
    verify_job_availability,
)
from tests.factories import make_company, make_job
from tests.fake_clients import FakeFirecrawlClient, crawl_result

NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    """A mutable clock tests advance to exercise cooldown windows."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def verify(session, job, *, pages=None, clock=NOW, cooldown=VERIFICATION_COOLDOWN_SECONDS):
    fake = FakeFirecrawlClient(pages)
    result = verify_job_availability(
        session,
        job,
        firecrawl=fake,
        cooldown_seconds=cooldown,
        clock=FakeClock(clock),
    )
    return result, fake


def _individual_page(url: str, *, closure: bool = False) -> object:
    markdown = (
        "def no longer accepting applications\n"
        if closure
        else "# AI Engineer\nBuild agentic systems in python."
    )
    return crawl_result(url, markdown=markdown, metadata={"title": "AI Engineer"})


def test_credible_individual_page_sets_available(session):
    job = make_job(session, company=make_company(session), job_url="https://jobs.example.com/openings/42")

    result, fake = verify(session, job, pages={job.job_url: _individual_page(job.job_url)})

    assert fake.requested == [job.job_url]
    assert result.outcome is VerificationOutcome.verified_available
    assert job.availability is AvailabilityStatus.available
    assert job.crawled_at == NOW
    assert job.last_checked_at == NOW
    assert job.closed_at is None
    assert job.crawl_status is CrawlStatus.scraped


def test_pages_scraped_are_never_another_url(session):
    job = make_job(session, job_url="https://jobs.example.com/openings/7")

    _, fake = verify(session, job, pages={job.job_url: _individual_page(job.job_url)})

    assert fake.requested == [job.job_url]


def test_explicit_closure_text_marks_closed_and_reopens_when_open_again(session):
    job = make_job(session, job_url="https://jobs.example.com/openings/9")

    closed, _ = verify(
        session,
        job,
        pages={job.job_url: _individual_page(job.job_url, closure=True)},
        cooldown=0,
    )

    assert closed.outcome is VerificationOutcome.verified_closed
    assert job.availability is AvailabilityStatus.closed
    assert job.closed_at == NOW
    assert job.crawl_status is CrawlStatus.scraped

    reopened, _ = verify(
        session,
        job,
        pages={job.job_url: _individual_page(job.job_url)},
        cooldown=0,
    )

    assert reopened.outcome is VerificationOutcome.verified_available
    assert job.availability is AvailabilityStatus.available
    assert job.closed_at is None
    assert job.crawled_at == NOW


def test_http_410_from_firecrawl_marks_closed(session):
    job = make_job(session, job_url="https://jobs.example.com/openings/11")
    gone = FirecrawlError("scrape returned HTTP 410", status_code=410, kind="http_error")

    result, fake = verify(session, job, pages={job.job_url: gone})

    assert fake.requested == [job.job_url]
    assert result.outcome is VerificationOutcome.verified_closed
    assert job.availability is AvailabilityStatus.closed
    assert job.closed_at == NOW


def test_http_404_does_not_close_and_preserves_state(session):
    job = make_job(
        session,
        job_url="https://jobs.example.com/openings/13",
        availability=AvailabilityStatus.available,
        crawled_at=NOW - timedelta(days=5),
    )
    missing = FirecrawlError("scrape returned HTTP 404", status_code=404, kind="http_error")

    result, _ = verify(session, job, pages={job.job_url: missing})

    assert result.outcome is VerificationOutcome.check_failed
    assert result.http_status == 404
    assert job.availability is AvailabilityStatus.available
    assert job.crawled_at == NOW - timedelta(days=5)
    assert job.last_checked_at == NOW
    assert job.crawl_status is CrawlStatus.failed


@pytest.mark.parametrize(
    "exc",
    [
        FirecrawlError("timed out", kind="timeout"),
        FirecrawlError("scrape returned HTTP 500", status_code=500, kind="http_error"),
        FirecrawlError("scrape returned HTTP 403", status_code=403, kind="http_error"),
        FirecrawlError("scrape returned HTTP 429", status_code=429, kind="http_error"),
        FirecrawlError("API rejected", kind="api_error"),
    ],
)
def test_failed_checks_preserve_availability_and_verification(session, exc):
    previous = NOW - timedelta(days=2)
    job = make_job(
        session,
        job_url="https://jobs.example.com/openings/15",
        availability=AvailabilityStatus.available,
        crawled_at=previous,
        crawl_status=CrawlStatus.scraped,
    )

    result, _ = verify(session, job, pages={job.job_url: exc})

    assert result.outcome is VerificationOutcome.check_failed
    assert job.availability is AvailabilityStatus.available
    assert job.crawled_at == previous
    assert job.last_checked_at == NOW
    assert job.crawl_status is CrawlStatus.failed


def test_empty_page_is_a_failed_check_not_a_verdict(session):
    previous = NOW - timedelta(days=1)
    job = make_job(
        session,
        job_url="https://jobs.example.com/openings/17",
        availability=AvailabilityStatus.available,
        crawled_at=previous,
    )
    empty = crawl_result(job.job_url, markdown=None, metadata={})

    result, _ = verify(session, job, pages={job.job_url: empty})

    assert result.outcome is VerificationOutcome.check_failed
    assert job.availability is AvailabilityStatus.available
    assert job.crawled_at == previous
    assert job.last_checked_at == NOW
    assert job.crawl_status is CrawlStatus.failed


def test_listing_or_unknown_page_never_becomes_available(session):
    previous = NOW - timedelta(days=3)
    listing = make_job(
        session,
        job_url="https://jobs.example.com/list-board",
        availability=AvailabilityStatus.available,
        crawled_at=previous,
    )
    listing_page = crawl_result(
        listing.job_url,
        markdown="# Roles\nSee all jobs at Acme Inc.",
        metadata={"title": "Roles"},
    )

    result, _ = verify(session, listing, pages={listing.job_url: listing_page})

    assert result.outcome is VerificationOutcome.still_unknown
    assert listing.availability is AvailabilityStatus.available
    assert listing.crawled_at == previous
    assert listing.last_checked_at == NOW
    assert "unchanged" in result.message

    unfamiliar = make_job(session, job_url="https://example.com/weird-page")
    page = crawl_result(unfamiliar.job_url, markdown="Just some content.")

    result, _ = verify(session, unfamiliar, pages={unfamiliar.job_url: page})

    assert result.outcome is VerificationOutcome.still_unknown
    assert unfamiliar.availability is AvailabilityStatus.unknown


def test_unfetchable_saved_url_fails_safely_without_any_call(session):
    job = make_job(session, job_url="file:///etc/passwd")

    result, fake = verify(session, job)

    assert result.outcome is VerificationOutcome.check_failed
    assert fake.requested == []
    assert job.availability is AvailabilityStatus.unknown
    assert job.crawled_at is None
    assert job.last_checked_at is None


def test_cooldown_rejects_repeat_checks_and_expires(session):
    job = make_job(session, job_url="https://jobs.example.com/openings/19")
    clock = FakeClock(NOW)
    fake = FakeFirecrawlClient({job.job_url: _individual_page(job.job_url)})

    first = verify_job_availability(
        session, job, firecrawl=fake, cooldown_seconds=60, clock=clock
    )
    assert first.outcome is VerificationOutcome.verified_available
    assert len(fake.requested) == 1

    with pytest.raises(JobVerificationCooldownError):
        verify_job_availability(session, job, firecrawl=fake, cooldown_seconds=60, clock=clock)
    assert len(fake.requested) == 1

    clock.advance(61)
    second = verify_job_availability(
        session, job, firecrawl=fake, cooldown_seconds=60, clock=clock
    )
    assert second.outcome is VerificationOutcome.verified_available
    assert len(fake.requested) == 2


def test_zero_cooldown_disables_the_guard(session):
    job = make_job(session, job_url="https://jobs.example.com/openings/21")
    fake = FakeFirecrawlClient({job.job_url: _individual_page(job.job_url)})

    verify_job_availability(session, job, firecrawl=fake, cooldown_seconds=0, clock=FakeClock(NOW))
    verify_job_availability(session, job, firecrawl=fake, cooldown_seconds=0, clock=FakeClock(NOW))

    assert len(fake.requested) == 2