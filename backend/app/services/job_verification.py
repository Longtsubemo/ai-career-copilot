"""Deterministic single-job availability re-verification.

An explicit user action re-checks a saved job against its own source URL:
Firecrawl fetches exactly ``jobs.job_url`` — never a browser-supplied target —
and the existing classification and closure-evidence machinery decides the
outcome. The page must look like one individual vacancy for a check to
confirm ``available``; a timeout, a 404 by itself, a 5xx, an access denial
or an empty page can never close a vacancy. A failed check preserves the
previous availability and the previous successful verification timestamp and
only records that a check was attempted, so the source can never be punished
for being slow or temporarily unreachable.

Concurrent or repeated checks of one job are serialized with a row lock, and
a conservative per-job cooldown (``job_verification_cooldown_seconds``)
rejects a new check shortly after the last one. Both stay in the database
via ``jobs.last_checked_at``, so the guard survives a process restart and is
testable without cross-process state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.agents.discovery.classify import (
    UrlQuality,
    classify_page,
    classify_url,
    has_closure_evidence,
    has_usable_content,
    resolve_quality,
)
from app.agents.runtime.runtime import utc_now
from app.clients.firecrawl import FirecrawlClient, FirecrawlError
from app.models.enums import AvailabilityStatus, CrawlStatus
from app.models.job import Job

VERIFICATION_COOLDOWN_SECONDS = 60


class VerificationOutcome(StrEnum):
    """What one re-verification check established about a vacancy."""

    verified_available = "verified_available"
    verified_closed = "verified_closed"
    still_unknown = "still_unknown"
    check_failed = "check_failed"


@dataclass(frozen=True)
class VerificationResult:
    """The public outcome of one check plus a client-safe explanation.

    ``checked_at`` is when the check ran; it is also the value written to
    ``jobs.last_checked_at``. ``http_status`` only carries the HTTP status of
    a crawl failure, so the caller never has to parse a message.
    """

    outcome: VerificationOutcome
    message: str
    checked_at: datetime
    http_status: int | None = None


class JobVerificationCooldownError(RuntimeError):
    """A job was re-verified too recently to re-check again."""


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _failure_message(detail: str) -> str:
    return (
        f"The check could not complete ({detail}). The previous availability "
        "and last successful verification were preserved; a slow or "
        "unreachable source never closes a vacancy."
    )


def _crawl_failure_detail(exc: FirecrawlError) -> str:
    if exc.kind == "timeout":
        return "the request timed out"
    if exc.status_code is not None:
        if exc.status_code in {401, 403, 429}:
            return "access was denied by the source site"
        return f"the source returned HTTP {exc.status_code}"
    if exc.kind in ("api_error", "shape_error"):
        return "the scraping service rejected the response"
    return "the source could not be reached"


def verify_job_availability(
    session: Session,
    job: Job,
    *,
    firecrawl: FirecrawlClient,
    cooldown_seconds: int = VERIFICATION_COOLDOWN_SECONDS,
    clock=utc_now,
) -> VerificationResult:
    """Re-check one job against its saved source URL and update its columns.

    Mutates the ``job`` object in place; the caller owns the transaction and
    commits. Raises :class:`JobVerificationCooldownError` when the job was
    checked within ``cooldown_seconds`` — nothing is written in that case.
    """
    now = clock()

    parts = urlsplit(job.job_url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return VerificationResult(
            outcome=VerificationOutcome.check_failed,
            message=(
                f"The saved URL {job.job_url!r} is not a fetchable HTTP(S) "
                "URL, so it cannot be re-verified. No availability change was "
                "made."
            ),
            checked_at=now,
        )

    session.refresh(job, with_for_update=True)
    if job.last_checked_at is not None and cooldown_seconds > 0:
        delta = _as_aware(now) - _as_aware(job.last_checked_at)
        if delta < timedelta(seconds=cooldown_seconds):
            remaining = max(
                1,
                int((timedelta(seconds=cooldown_seconds) - delta).total_seconds()) + 1,
            )
            raise JobVerificationCooldownError(
                f"job {job.id} was checked less than {cooldown_seconds}s ago; "
                f"re-verify again in about {remaining}s"
            )

    try:
        crawl = firecrawl.scrape(job.job_url)
    except FirecrawlError as exc:
        if exc.status_code == 410:
            return _close(session, job, now, "the source returns HTTP 410 (Gone)")
        return _fail(session, job, now, _failure_message(_crawl_failure_detail(exc)), exc.status_code)

    if not has_usable_content(crawl):
        return _fail(
            session,
            job,
            now,
            _failure_message("the page returned no usable content"),
        )

    quality = resolve_quality(classify_url(job.job_url), classify_page(crawl))
    if quality is UrlQuality.individual:
        if has_closure_evidence(crawl):
            return _close(
                session, job, now, "the live page states the vacancy is closed"
            )
        return _available(session, job, now)

    return _still_unknown(session, job, now, quality)


def _available(session: Session, job: Job, now: datetime) -> VerificationResult:
    job.availability = AvailabilityStatus.available
    job.crawled_at = now
    job.closed_at = None
    job.crawl_status = CrawlStatus.scraped
    job.last_checked_at = now
    session.flush()
    return VerificationResult(
        outcome=VerificationOutcome.verified_available,
        message=(
            "Re-verified on the live page: the vacancy looks open as of this "
            "check."
        ),
        checked_at=now,
    )


def _close(session: Session, job: Job, now: datetime, evidence: str) -> VerificationResult:
    job.availability = AvailabilityStatus.closed
    job.closed_at = now
    job.crawled_at = now
    job.crawl_status = CrawlStatus.scraped
    job.last_checked_at = now
    session.flush()
    return VerificationResult(
        outcome=VerificationOutcome.verified_closed,
        message=f"The live page reports this vacancy is closed ({evidence}).",
        checked_at=now,
    )


def _still_unknown(
    session: Session, job: Job, now: datetime, quality: UrlQuality
) -> VerificationResult:
    job.crawl_status = CrawlStatus.scraped
    job.last_checked_at = now
    session.flush()
    return VerificationResult(
        outcome=VerificationOutcome.still_unknown,
        message=(
            "The page could not be confirmed as an individual vacancy "
            f"(classed as {quality.value}); the stored availability was left "
            "unchanged."
        ),
        checked_at=now,
    )


def _fail(
    session: Session,
    job: Job,
    now: datetime,
    message: str,
    http_status: int | None = None,
) -> VerificationResult:
    job.crawl_status = CrawlStatus.failed
    job.last_checked_at = now
    session.flush()
    return VerificationResult(
        outcome=VerificationOutcome.check_failed,
        message=message,
        checked_at=now,
        http_status=http_status,
    )


__all__ = [
    "VERIFICATION_COOLDOWN_SECONDS",
    "JobVerificationCooldownError",
    "VerificationOutcome",
    "VerificationResult",
    "verify_job_availability",
]