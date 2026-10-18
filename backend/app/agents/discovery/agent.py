"""Job Discovery agent: search, crawl, normalize, deduplicate, persist.

The agent executes through the generic :class:`AgentRuntime` as the
``job_discovery`` handler. It owns no lifecycle of its own: claiming,
retries, idempotency and state transitions stay with the runtime.

Two strict phases keep database work away from external HTTP:

* phase 1 — validate the request, run the bounded query set through SearXNG,
  canonicalize and dedupe candidate URLs (no database access at all);
* phase 2 — crawl every candidate through Firecrawl and extract/filter
  locally (still no database access);
* phase 3 — persist everything in one batch with PostgreSQL upserts on the
  existing unique constraints (``companies.normalized_name``,
  ``jobs.canonical_url``), so repeated or concurrent discovery can never
  create duplicates or leave the session unusable.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.agents.discovery.classify import (
    has_closure_evidence,
    sanitize_public_url,
)
from app.agents.discovery.contract import (
    DISCOVERY_AGENT_NAME,
    MAX_DISCOVERY_ISSUES,
    DiscoveryIssue,
    DiscoveryIssueCategory,
    DiscoveryRequest,
    DiscoveryResult,
)
from app.agents.discovery.extract import (
    ExtractedJob,
    PageEvidenceFailure,
    extract_page_evidence,
    filter_check,
    validate_page_evidence,
)
from app.agents.discovery.query import build_search_queries
from app.agents.discovery.sources import (
    DEFAULT_SOURCE_REGISTRY,
    SourceKind,
    SourceRegistry,
    assess_source_url,
)
from app.agents.runtime.context import RunContext
from app.agents.runtime.registry import HandlerRegistry
from app.agents.runtime.runtime import utc_now
from app.clients.firecrawl import CrawlResult, FirecrawlClient, FirecrawlError
from app.clients.firecrawl_search import FirecrawlSearchClient
from app.clients.search import SearchClient, SearchError, SearchResult
from app.clients.searxng import SearXNGClient
from app.core.config import get_settings
from app.models.company import Company
from app.models.enums import AvailabilityStatus, CrawlStatus
from app.models.job import Job
from app.services.normalization import canonicalize_url, normalize_company_name

logger = logging.getLogger(__name__)

MAX_CANONICAL_URL_LENGTH = 500
MAX_JOB_URL_LENGTH = 1000
MESSAGE_MAX = 300
_ACCESS_DENIED_STATUS = frozenset({401, 403, 429})


@dataclass
class _Counters:
    """Mutable tally of every candidate-level outcome in one run."""

    duplicates_skipped: int = 0
    scrape_attempts: int = 0
    extractions_succeeded: int = 0
    page_validation_failures: int = 0
    listings_skipped: int = 0
    invalid_results: int = 0
    malformed_results: int = 0
    scrape_failures: int = 0


@dataclass(frozen=True)
class _Candidate:
    """A deduplicated, persistable URL admitted to the crawl phase."""

    result: SearchResult
    canonical_url: str


@dataclass(frozen=True)
class _CrawlOutcome:
    """Result of one crawl attempt; ``scraped`` is True only on success."""

    crawl: CrawlResult | None
    error: str | None
    category: DiscoveryIssueCategory | None = None
    status_code: int | None = None

    @property
    def scraped(self) -> bool:
        return self.crawl is not None and self.error is None


@dataclass(frozen=True)
class _Plan:
    """A candidate approved for persistence with its extracted fields."""

    candidate: _Candidate
    extracted: ExtractedJob
    outcome: _CrawlOutcome


def _record_issue(
    issues: list[DiscoveryIssue],
    url: str,
    stage: str,
    category: DiscoveryIssueCategory,
    *,
    status: int | None = None,
    message: str | None = None,
) -> None:
    """Append one bounded, sanitized diagnostic; drop overflow silently."""
    if len(issues) >= MAX_DISCOVERY_ISSUES:
        return
    issues.append(
        DiscoveryIssue(
            url=sanitize_public_url(url),
            stage=stage,
            category=category,
            status=status,
            message=(message or "")[:MESSAGE_MAX],
        )
    )


def _crawl_failure_category(exc: FirecrawlError) -> DiscoveryIssueCategory:
    """Map a FirecrawlError to a factual discovery issue category."""
    if exc.kind == "timeout":
        return DiscoveryIssueCategory.timeout
    if exc.status_code is not None:
        if exc.status_code in _ACCESS_DENIED_STATUS:
            return DiscoveryIssueCategory.access_denied
        return DiscoveryIssueCategory.http_error
    if exc.kind in ("api_error", "shape_error"):
        return DiscoveryIssueCategory.scrape_failure
    return DiscoveryIssueCategory.network_error


class DiscoveryAgent:
    """Deterministic job discovery driven by injected tool clients.

    The agent depends on abstractions only: a search client for candidates, a
    Firecrawl client for page content, a clock for timestamps. The database
    session arrives per call from the runtime's :class:`RunContext`, never
    from the constructor.
    """

    def __init__(
        self,
        searxng: SearchClient,
        firecrawl: FirecrawlClient,
        *,
        clock: Callable[[], datetime] = utc_now,
        source: str = "searxng",
        max_candidates: int = 10,
        max_queries: int = 2,
        source_registry: SourceRegistry = DEFAULT_SOURCE_REGISTRY,
    ) -> None:
        if max_candidates < 1:
            raise ValueError("max_candidates must be >= 1")
        if max_queries < 1:
            raise ValueError("max_queries must be >= 1")
        if not source:
            raise ValueError("source must not be empty")
        self._search = searxng
        self._firecrawl = firecrawl
        self._clock = clock
        self._source = source
        self._max_candidates = max_candidates
        self._max_queries = max_queries
        self._source_registry = source_registry

    @property
    def source(self) -> str:
        return self._source

    @property
    def max_candidates(self) -> int:
        return self._max_candidates

    @property
    def max_queries(self) -> int:
        return self._max_queries

    def handle(
        self, payload: dict | None, context: RunContext
    ) -> dict:
        """Runtime handler contract: validate the payload, then discover."""
        request = DiscoveryRequest.from_payload(payload)
        return self.discover(context.session, request)

    def discover(self, session: Session, request: DiscoveryRequest) -> dict:
        """Execute discovery and return the JSON-ready structured result.

        The bounded query set is run in order. A failure of the primary query
        raises :class:`SearchError` when the operation genuinely cannot
        proceed, letting the runtime apply its retry policy; a failure of an
        additional query is recorded in ``errors`` and the run continues with
        the results already gathered. Candidate-level failures (crawl errors,
        unusable pages, malformed URLs) are likewise recorded and never abort
        the run.
        """
        errors: list[str] = []
        issues: list[DiscoveryIssue] = []
        counters = _Counters()
        queries = build_search_queries(
            request,
            max_queries=self._max_queries,
            source_registry=self._source_registry,
        )

        results: list[SearchResult] = []
        for index, query in enumerate(queries):
            try:
                results.extend(self._search.search(query))
            except SearchError:
                logger.warning(
                    "discovery search failed for query %r", query, exc_info=True
                )
                if index == 0:
                    raise
                errors.append(f"search failed for additional query {query!r}")
        searched = len(results)

        candidates = self._collect_candidates(results, errors, issues, counters)

        plans: list[_Plan] = []
        failed_candidates: list[_Candidate] = []
        jobs_skipped = 0
        for candidate in candidates:
            url = candidate.result.url
            counters.scrape_attempts += 1
            outcome = self._crawl(candidate)
            if outcome.error is not None:
                errors.append(outcome.error)
                counters.scrape_failures += 1
                jobs_skipped += 1
                failed_candidates.append(candidate)
                _record_issue(
                    issues,
                    url,
                    "crawl",
                    outcome.category or DiscoveryIssueCategory.scrape_failure,
                    status=outcome.status_code,
                    message=outcome.error,
                )
                continue

            evidence = extract_page_evidence(outcome.crawl)
            validation = validate_page_evidence(outcome.crawl, evidence)
            if not validation.accepted:
                counters.page_validation_failures += 1
                failed_candidates.append(candidate)
                if validation.failure is PageEvidenceFailure.listing:
                    counters.listings_skipped += 1
                    category = DiscoveryIssueCategory.listing_page
                    message = "page is a listing, not an individual vacancy"
                elif validation.failure is PageEvidenceFailure.empty:
                    jobs_skipped += 1
                    category = DiscoveryIssueCategory.empty_extraction
                    message = "page did not provide usable vacancy evidence"
                elif validation.failure in {
                    PageEvidenceFailure.challenge,
                    PageEvidenceFailure.error,
                }:
                    jobs_skipped += 1
                    category = DiscoveryIssueCategory.validation_rejected
                    message = "page could not be validated as an individual vacancy"
                else:
                    jobs_skipped += 1
                    category = DiscoveryIssueCategory.validation_rejected
                    message = "page did not provide sufficient vacancy evidence"
                _record_issue(
                    issues,
                    url,
                    "validate_page",
                    category,
                    status=None,
                    message=message,
                )
                continue

            counters.extractions_succeeded += 1
            assert validation.extracted is not None
            extracted = validation.extracted
            decision = filter_check(request, extracted)
            if not decision.accepted:
                logger.info(
                    "discovery skipped candidate at %s",
                    sanitize_public_url(candidate.canonical_url),
                )
                jobs_skipped += 1
                _record_issue(
                    issues,
                    url,
                    "filter",
                    DiscoveryIssueCategory.validation_rejected,
                    message="candidate did not meet the requested filters",
                )
                continue

            plans.append(_Plan(candidate, extracted, outcome))

        created, updated, job_ids = self._persist(
            session, plans, failed_candidates=failed_candidates
        )

        result = DiscoveryResult(
            query={
                "role": request.role,
                "location": request.location,
                "experience": request.experience,
            },
            queries=queries,
            searched=searched,
            candidates_found=len(candidates),
            jobs_created=created,
            jobs_updated=updated,
            jobs_skipped=jobs_skipped,
            errors=errors,
            job_ids=job_ids,
            duplicates_skipped=counters.duplicates_skipped,
            scrape_attempts=counters.scrape_attempts,
            extractions_succeeded=counters.extractions_succeeded,
            page_validation_failures=counters.page_validation_failures,
            listings_skipped=counters.listings_skipped,
            invalid_results=counters.invalid_results,
            malformed_results=counters.malformed_results,
            scrape_failures=counters.scrape_failures,
            issues=issues,
        )
        return result.model_dump(mode="json")

    def _collect_candidates(
        self,
        results: list[SearchResult],
        errors: list[str],
        issues: list[DiscoveryIssue],
        counters: _Counters,
    ) -> list[_Candidate]:
        """Validate and dedupe search results by canonical URL, capped.

        Only reviewed direct-posting URLs within the persisted URL column
        limits are canonicalized into crawl candidates or allowed to consume
        the candidate cap. Unsupported sources, landing/listing pages, unknown
        paths, malformed URLs, and overlong URLs are recorded and skipped
        first. Canonical duplicates are counted and dropped; the first
        eligible occurrence wins.
        """
        candidates: dict[str, _Candidate] = {}
        seen_canonical: set[str] = set()
        for result in results:
            assessment = assess_source_url(result.url, self._source_registry)
            if assessment.kind is SourceKind.INVALID_URL:
                errors.append("malformed search result URL skipped")
                counters.malformed_results += 1
                _record_issue(
                    issues,
                    result.url,
                    "eligibility",
                    DiscoveryIssueCategory.invalid_url,
                    message="malformed search result URL",
                )
                continue
            if not assessment.eligible:
                if assessment.kind in {
                    SourceKind.CAREER_LANDING,
                    SourceKind.LISTING,
                }:
                    category = DiscoveryIssueCategory.listing_page
                    message = (
                        "career landing page is not an individual vacancy"
                        if assessment.kind is SourceKind.CAREER_LANDING
                        else "listing URL is not an individual vacancy"
                    )
                else:
                    category = DiscoveryIssueCategory.validation_rejected
                    message = "source or URL pattern is not enabled"
                _record_issue(
                    issues,
                    result.url,
                    "eligibility",
                    category,
                    message=message,
                )
                continue

            try:
                parts = urlsplit(result.url)
                valid_url = (
                    parts.scheme.lower() in ("http", "https")
                    and bool(parts.netloc)
                    and bool(parts.hostname)
                )
            except ValueError:
                valid_url = False
            if not valid_url:
                errors.append("malformed search result URL skipped")
                counters.malformed_results += 1
                _record_issue(
                    issues,
                    result.url,
                    "validate",
                    DiscoveryIssueCategory.invalid_url,
                    message="malformed search result URL",
                )
                continue
            try:
                canonical = canonicalize_url(result.url)
            except ValueError:
                canonical = ""
            if not canonical:
                errors.append("search result URL has no canonical form")
                counters.malformed_results += 1
                _record_issue(
                    issues,
                    result.url,
                    "validate",
                    DiscoveryIssueCategory.invalid_url,
                    message="URL has no canonical form",
                )
                continue
            rejection = None
            if len(canonical) > MAX_CANONICAL_URL_LENGTH:
                rejection = (
                    f"canonical URL longer than {MAX_CANONICAL_URL_LENGTH} "
                    "characters, not persisted"
                )
            elif len(result.url) > MAX_JOB_URL_LENGTH:
                rejection = (
                    f"job URL longer than {MAX_JOB_URL_LENGTH} characters, "
                    "not persisted"
                )
            if rejection is not None:
                errors.append(rejection)
                counters.invalid_results += 1
                _record_issue(
                    issues,
                    result.url,
                    "validate",
                    DiscoveryIssueCategory.invalid_url,
                    message=rejection,
                )
                continue

            if canonical in seen_canonical:
                counters.duplicates_skipped += 1
                _record_issue(
                    issues,
                    result.url,
                    "dedupe",
                    DiscoveryIssueCategory.duplicate,
                    message="duplicate canonical URL in this result set",
                )
                continue
            seen_canonical.add(canonical)

            candidates[canonical] = _Candidate(
                result=result,
                canonical_url=canonical,
            )
            if len(candidates) >= self._max_candidates:
                break
        return list(candidates.values())

    def _crawl(self, candidate: _Candidate) -> _CrawlOutcome:
        """Crawl one candidate; never raises, failures become an error."""
        url = candidate.result.url
        try:
            crawl = self._firecrawl.scrape(url)
        except FirecrawlError as exc:
            category = _crawl_failure_category(exc)
            logger.warning(
                "Firecrawl scrape failed %s",
                json.dumps(
                    {
                        "stage": "crawl",
                        "category": category.value,
                        "error_kind": exc.kind or "unknown",
                        "status_code": exc.status_code,
                        "correlation_id": exc.correlation_id,
                        "provider_request_id": exc.request_id,
                        "provider_error_code": exc.provider_error_code,
                        "provider_message": exc.provider_message,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            if exc.status_code is not None:
                public_detail = f"crawl failed with HTTP {exc.status_code}"
            elif exc.kind == "timeout":
                public_detail = "crawl request timed out"
            elif exc.kind == "network_error":
                public_detail = "crawl request failed due to a network error"
            elif exc.kind == "api_error":
                public_detail = "scrape failed"
            elif exc.kind == "shape_error":
                public_detail = "scrape response was invalid"
            else:
                public_detail = "crawl failed"
            return _CrawlOutcome(
                None,
                public_detail,
                category,
                exc.status_code,
            )
        return _CrawlOutcome(crawl, None)

    def _persist(
        self,
        session: Session,
        plans: list[_Plan],
        *,
        failed_candidates: list[_Candidate] | None = None,
    ) -> tuple[int, int, list[str]]:
        """Upsert accepted vacancies and record failed attempts on existing rows.

        Failed or unusable first-seen candidates are never inserted. A failed
        recrawl updates only operational fields on an existing row, preserving
        its trusted content, availability, and last successful crawl time.
        """
        now = self._clock()
        created = 0
        updated = 0
        job_ids: list[str] = []
        for plan in plans:
            company_id = self._resolve_company(session, plan.extracted.company_name)
            crawl_status = (
                CrawlStatus.scraped if plan.outcome.scraped else CrawlStatus.failed
            )
            availability: AvailabilityStatus | None = None
            if plan.outcome.scraped:
                if has_closure_evidence(plan.outcome.crawl):
                    availability = AvailabilityStatus.closed
                else:
                    availability = AvailabilityStatus.available
            job_id, inserted = self._upsert_job(
                session,
                plan=plan,
                company_id=company_id,
                crawl_status=crawl_status,
                crawled_at=now if plan.outcome.scraped else None,
                availability=availability,
                now=now,
            )
            job_ids.append(str(job_id))
            if inserted:
                created += 1
            else:
                updated += 1
        for candidate in failed_candidates or ():
            session.execute(
                update(Job)
                .where(Job.canonical_url == candidate.canonical_url)
                .values(
                    crawl_status=CrawlStatus.failed,
                    last_seen_at=now,
                    updated_at=now,
                )
            )
        return created, updated, job_ids

    @staticmethod
    def _resolve_company(session: Session, company_name: str | None) -> UUID | None:
        """Return the company's id, reusing or creating it atomically.

        ``INSERT .. ON CONFLICT (normalized_name) DO UPDATE`` returns the
        surviving row's id in every case, so concurrent discovery runs
        reuse one company instead of racing on the unique constraint.
        When no company name was discovered the relationship stays null.
        """
        if not company_name:
            return None
        normalized = normalize_company_name(company_name)
        if not normalized:
            return None
        table = Company.__table__
        stmt = (
            pg_insert(table)
            .values(name=company_name, normalized_name=normalized)
            .on_conflict_do_update(
                index_elements=[table.c.normalized_name],
                set_={table.c.name: table.c.name},
            )
            .returning(table.c.id)
        )
        return session.execute(stmt).scalar_one()

    def _upsert_job(
        self,
        session: Session,
        *,
        plan: _Plan,
        company_id: UUID | None,
        crawl_status: CrawlStatus,
        crawled_at: datetime | None,
        availability: AvailabilityStatus | None,
        now: datetime,
    ) -> tuple[UUID, bool]:
        """Insert or update one job; returns ``(id, was_inserted)``.

        ``canonical_url`` is the conflict target. On update, only non-null
        extracted values overwrite existing columns, ``last_seen_at`` is
        refreshed, ``first_seen_at`` is preserved, and ``crawled_at``
        advances only on a successful crawl (``coalesce`` keeps the last
        success timestamp when the re-crawl failed). ``crawl_status``
        always records the latest attempt.

        Vacancy content (title, description, location, employment type,
        experience, company, outbound URL, source, date posted) is reconciled
        only for a page that passed vacancy-evidence validation. Failed and
        unusable attempts are handled separately by ``_persist``: they never
        insert a first-seen job and only update operational fields on an
        existing row. Search-result snippets are never persisted as vacancy
        content.

        Availability is only written when the crawl produced a credible
        verdict: a successful validated vacancy crawl sets ``available`` (or
        ``closed`` plus ``closed_at`` on affirmative closure evidence),
        while a failed crawl passes ``None`` and leaves the previous
        availability and ``closed_at`` untouched.
        """
        table = Job.__table__
        stmt = pg_insert(table).values(
            title=plan.extracted.title,
            description=plan.extracted.description,
            location=plan.extracted.location,
            employment_type=plan.extracted.employment_type,
            experience_level=plan.extracted.experience_level,
            company_id=company_id,
            job_url=plan.extracted.job_url,
            canonical_url=plan.candidate.canonical_url,
            source=self._source,
            is_active=True,
            crawl_status=crawl_status,
            crawled_at=crawled_at,
            date_posted=plan.extracted.date_posted,
            availability=availability or AvailabilityStatus.unknown,
            closed_at=now if availability is AvailabilityStatus.closed else None,
            first_seen_at=now,
            last_seen_at=now,
            updated_at=now,
        )
        set_: dict[str, object] = {
            "crawl_status": stmt.excluded.crawl_status,
            "crawled_at": func.coalesce(
                stmt.excluded.crawled_at, table.c.crawled_at
            ),
            "last_seen_at": stmt.excluded.last_seen_at,
            "updated_at": stmt.excluded.updated_at,
        }
        if plan.outcome.scraped:
            set_.update(
                {
                    "title": func.coalesce(stmt.excluded.title, table.c.title),
                    "description": func.coalesce(
                        stmt.excluded.description, table.c.description
                    ),
                    "location": func.coalesce(
                        stmt.excluded.location, table.c.location
                    ),
                    "employment_type": func.coalesce(
                        stmt.excluded.employment_type, table.c.employment_type
                    ),
                    "experience_level": func.coalesce(
                        stmt.excluded.experience_level, table.c.experience_level
                    ),
                    "company_id": func.coalesce(
                        stmt.excluded.company_id, table.c.company_id
                    ),
                    "job_url": func.coalesce(
                        stmt.excluded.job_url, table.c.job_url
                    ),
                    "source": func.coalesce(stmt.excluded.source, table.c.source),
                    "date_posted": func.coalesce(
                        stmt.excluded.date_posted, table.c.date_posted
                    ),
                }
            )
        if availability is AvailabilityStatus.closed:
            set_["availability"] = AvailabilityStatus.closed
            set_["closed_at"] = now
        elif availability is AvailabilityStatus.available:
            set_["availability"] = AvailabilityStatus.available
            set_["closed_at"] = None
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.canonical_url],
            set_=set_,
        ).returning(table.c.id, text("(xmax = 0) AS inserted"))
        row = session.execute(stmt).first()
        assert row is not None
        job_id, inserted = row
        return UUID(str(job_id)), bool(inserted)


def register_discovery_handler(
    registry: HandlerRegistry,
    *,
    searxng: SearchClient | None = None,
    firecrawl: FirecrawlClient | None = None,
    clock: Callable[[], datetime] = utc_now,
    source: str = "searxng",
    max_candidates: int = 10,
    max_queries: int = 2,
    source_registry: SourceRegistry = DEFAULT_SOURCE_REGISTRY,
    agent_name: str = DISCOVERY_AGENT_NAME,
) -> DiscoveryAgent:
    """Build a DiscoveryAgent and register its handler on ``registry``.

    Missing clients are constructed from application settings. The selected
    search provider is either SearXNG or Firecrawl Search; the scrape client
    remains Firecrawl. Tests inject deterministic fake clients, so no test
    reaches a live service. Only the given registry is mutated.
    """
    if searxng is None or firecrawl is None:
        settings = get_settings()
        if searxng is None:
            if settings.search_provider == "firecrawl":
                searxng = FirecrawlSearchClient(
                    settings.firecrawl_base_url,
                    api_key=settings.firecrawl_api_key,
                )
            else:
                searxng = SearXNGClient(settings.searxng_base_url)
        if firecrawl is None:
            firecrawl = FirecrawlClient(
                settings.firecrawl_base_url, api_key=settings.firecrawl_api_key
            )
    agent = DiscoveryAgent(
        searxng,
        firecrawl,
        clock=clock,
        source=source,
        max_candidates=max_candidates,
        max_queries=max_queries,
        source_registry=source_registry,
    )
    registry.register(agent_name, agent.handle)
    return agent
