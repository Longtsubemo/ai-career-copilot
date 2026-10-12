"""Behaviour of the Job Discovery agent.

Pure functions (contract, query, extraction, filtering) are tested
directly; persistence is tested against PostgreSQL through the
savepoint-backed ``session`` fixture. External services are the scripted
fakes from ``tests.fake_clients`` — nothing here touches the network.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.agents.discovery import (
    DISCOVERY_AGENT_NAME,
    DiscoveryAgent,
    DiscoveryInputError,
    DiscoveryRequest,
    RoleMatch,
    build_search_query,
    discovery_idempotency_key,
    extract_job,
    filter_check,
    register_discovery_handler,
)
from app.agents.discovery.extract import ExtractedJob, parse_year_range
from app.agents.runtime import HandlerRegistry, RunContext
from app.clients.firecrawl import FirecrawlError
from app.clients.searxng import SearXNGError
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.enums import AvailabilityStatus, CrawlStatus, RunStatus
from app.models.job import Job
from app.services.normalization import canonicalize_url, normalize_company_name
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
URL_B = "https://jobs.example.com/role/99"

REQUEST = DiscoveryRequest(
    role="AI Engineer", location="Bangalore", experience="1-3 years"
)

HAPPY_RESULTS = [
    search_result(URL_A, "AI Engineer", "Build agentic systems."),
    search_result(URL_B, "ML Engineer", "Train production models."),
]

HAPPY_PAGES = {
    URL_A: listing_page(
        URL_A,
        title="Senior AI Engineer",
        description="Own the agent platform.",
        location="Bangalore",
        company="Acme Inc.",
        employment_type="Full-time",
        experience="1-3 years",
    ),
    URL_B: listing_page(URL_B, title="ML Engineer", location="Bangalore"),
}


def make_agent(
    results=(),
    pages=None,
    *,
    clock=None,
    error: SearXNGError | None = None,
    **kwargs: object,
) -> tuple[DiscoveryAgent, FakeSearXNGClient, FakeFirecrawlClient]:
    searxng = FakeSearXNGClient(results, error=error)
    firecrawl = FakeFirecrawlClient(pages)
    agent = DiscoveryAgent(
        searxng, firecrawl, clock=clock or (lambda: T1), **kwargs  # type: ignore[arg-type]
    )
    return agent, searxng, firecrawl


def job_count(session, canonical_url: str) -> int:
    return session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.canonical_url == canonical_url)
    )


def get_job(session, canonical_url: str) -> Job:
    job = session.scalar(select(Job).where(Job.canonical_url == canonical_url))
    assert job is not None
    return job


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


def run_context(session) -> RunContext:
    run = AgentRun(
        agent_name=DISCOVERY_AGENT_NAME,
        status=RunStatus.pending,
        idempotency_key="test:discovery:context",
    )
    return RunContext(
        session=session, run=run, agent_name=DISCOVERY_AGENT_NAME, attempt=1
    )


class TestContract:
    def test_build_search_query_combines_the_constraints(self) -> None:
        assert build_search_query(REQUEST) == "AI Engineer Bangalore jobs 1-3 years"

    def test_from_payload_normalizes_whitespace_and_case_folding_keys(self) -> None:
        request = DiscoveryRequest.from_payload(
            {
                "role": "  AI   Engineer ",
                "location": " Bangalore ",
                "experience": "  1-3   years ",
            }
        )
        assert request.role == "AI Engineer"
        assert request.location == "Bangalore"
        assert request.experience == "1-3 years"

    @pytest.mark.parametrize("payload", [None, "role", 7, ["role"]])
    def test_from_payload_rejects_non_mappings(self, payload: object) -> None:
        with pytest.raises(DiscoveryInputError, match="must be a mapping"):
            DiscoveryRequest.from_payload(payload)

    @pytest.mark.parametrize(
        "payload",
        [
            {"role": "AI Engineer", "location": "Bangalore"},
            {"role": "  ", "location": "Bangalore", "experience": "1-3 years"},
            {"role": "AI", "location": "BLR", "experience": "1-3 years", "extra": 1},
            {"role": 5, "location": "BLR", "experience": "1-3 years"},
        ],
        ids=["missing", "blank", "extra", "wrong-type"],
    )
    def test_from_payload_rejects_invalid_requests(self, payload: dict) -> None:
        with pytest.raises(DiscoveryInputError):
            DiscoveryRequest.from_payload(payload)

    def test_idempotency_key_is_normalized_and_stable(self) -> None:
        noisy = DiscoveryRequest.from_payload(
            {"role": " AI   ENGINEER ", "location": "bangalore ", "experience": "1-3 years"}
        )
        clean = DiscoveryRequest(
            role="ai engineer", location="Bangalore", experience="1-3 years"
        )
        assert discovery_idempotency_key(noisy) == discovery_idempotency_key(clean)
        assert discovery_idempotency_key(clean) == (
            "job_discovery:ai engineer:bangalore:1-3 years"
        )

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1-3 years", (1, 3)),
            ("3-1 years", (1, 3)),
            ("5+", (5, 99)),
            ("7 plus years", (7, 99)),
            ("2 years", (2, 2)),
            ("4 yrs", (4, 4)),
            ("senior", None),
            ("", None),
            (None, None),
        ],
    )
    def test_parse_year_range_variants(self, text: str | None, expected: object) -> None:
        assert parse_year_range(text) == expected


class TestExtraction:
    def test_metadata_wins_over_heading_and_search_title(self) -> None:
        crawl = listing_page(URL_A, title="Metadata Title", location="Bangalore")
        result = search_result(URL_A, "Search Title", "Search snippet.")

        extracted = extract_job(result, crawl)

        assert extracted.title == "Metadata Title"
        assert extracted.description == "Search snippet."
        assert extracted.location == "Bangalore"

    def test_heading_is_used_when_metadata_has_no_title(self) -> None:
        crawl = crawl_result(URL_A, markdown="# Heading Title\n\nBody")
        result = search_result(URL_A, "Search Title")

        extracted = extract_job(result, crawl)

        assert extracted.title == "Heading Title"

    def test_search_title_is_the_final_fallback(self) -> None:
        crawl = crawl_result(URL_A, markdown="No headings here", metadata={})
        result = search_result(URL_A, "Search Title")

        extracted = extract_job(result, crawl)

        assert extracted.title == "Search Title"

    def test_markdown_lines_supply_structured_fields(self) -> None:
        crawl = crawl_result(
            URL_A,
            markdown=(
                "# Role\n"
                "Location: Bangalore, India\n"
                "Employment type: Contract\n"
                "Experience: 3-5 years\n"
                "Company: Globex Pvt Ltd\n"
            ),
        )
        result = search_result(URL_A, "Search Title")

        extracted = extract_job(result, crawl)

        assert extracted.location == "Bangalore, India"
        assert extracted.employment_type == "Contract"
        assert extracted.experience_level == "3-5 years"
        assert extracted.company_name == "Globex Pvt Ltd"

    def test_absent_sources_stay_none_and_never_fabricate(self) -> None:
        extracted = extract_job(search_result(URL_A, "Search Title"), None)

        assert extracted.title == "Search Title"
        assert extracted.description is None
        assert extracted.location is None
        assert extracted.employment_type is None
        assert extracted.experience_level is None
        assert extracted.company_name is None

    def test_overlong_title_is_clipped_to_the_column_limit(self) -> None:
        result = search_result(URL_A, "x" * 400)

        extracted = extract_job(result, None)

        assert len(extracted.title) == 255

    def test_metadata_location_wins_over_markdown_line(self) -> None:
        crawl = crawl_result(
            URL_A,
            markdown="Location: Mumbai",
            metadata={"location": "Bangalore"},
        )
        result = search_result(URL_A, "Role")

        assert extract_job(result, crawl).location == "Bangalore"


class TestFiltering:
    def make(self, **overrides: object) -> ExtractedJob:
        base: dict = {
            "title": "Senior AI Engineer",
            "job_url": URL_A,
            "location": "Bangalore",
            "experience_level": "2 years",
        }
        base.update(overrides)
        return ExtractedJob(**base)

    @pytest.mark.parametrize(
        "title",
        [
            "AI Engineer",
            "Senior AI Engineer",
            "Artificial Intelligence Engineer",
            "AI Platform Engineer",
        ],
    )
    def test_role_accepts_title_variants_in_the_same_family(self, title: str) -> None:
        decision = filter_check(REQUEST, self.make(title=title))
        assert decision.accepted
        assert decision.role_match is RoleMatch.exact

    @pytest.mark.parametrize(
        "title", ["Machine Learning Engineer", "ML Engineer", "LLM Engineer"]
    )
    def test_role_accepts_related_family_titles_as_related(self, title: str) -> None:
        decision = filter_check(REQUEST, self.make(title=title))
        assert decision.accepted
        assert decision.role_match is RoleMatch.related
        assert "related role family" in (decision.reason or "")

    @pytest.mark.parametrize(
        "title", ["Engineer", "Data Scientist", "Product Manager"]
    )
    def test_role_rejects_titles_without_the_family_signal(self, title: str) -> None:
        decision = filter_check(REQUEST, self.make(title=title))
        assert not decision.accepted
        assert "role signal" in (decision.reason or "")

    def test_role_outside_the_taxonomy_falls_back_to_token_overlap(self) -> None:
        request = DiscoveryRequest(
            role="Data Scientist", location="Bangalore", experience="1-3 years"
        )
        accepted = filter_check(
            request, self.make(title="Senior Data Scientist")
        )
        rejected = filter_check(request, self.make(title="Product Manager"))

        assert accepted.accepted
        assert accepted.role_match is None
        assert not rejected.accepted
        assert "matches none of" in (rejected.reason or "")

    @pytest.mark.parametrize(
        "location",
        [
            "Bangalore",
            "Bangalore, India",
            "Bangalore Urban, Karnataka",
            "Bengaluru",
            "Bengaluru Urban, Karnataka",
            "BENGALURU, INDIA",
        ],
    )
    def test_location_accepts_overlapping_places_and_aliases(
        self, location: str
    ) -> None:
        assert filter_check(REQUEST, self.make(location=location)).accepted

    @pytest.mark.parametrize("location", ["Mumbai", "Pune, India"])
    def test_location_rejects_disjoint_places(self, location: str) -> None:
        decision = filter_check(REQUEST, self.make(location=location))
        assert not decision.accepted
        assert "does not overlap" in (decision.reason or "")

    def test_unknown_job_location_is_never_a_rejection(self) -> None:
        assert filter_check(REQUEST, self.make(location=None)).accepted

    @pytest.mark.parametrize(
        "experience", ["1-3 years", "2-5 years", "3 years", "1+"]
    )
    def test_experience_accepts_overlapping_ranges(self, experience: str) -> None:
        assert filter_check(REQUEST, self.make(experience_level=experience)).accepted

    @pytest.mark.parametrize("experience", ["7-10 years", "10+ years"])
    def test_experience_rejects_disjoint_ranges(self, experience: str) -> None:
        decision = filter_check(REQUEST, self.make(experience_level=experience))
        assert not decision.accepted
        assert "does not overlap" in (decision.reason or "")

    @pytest.mark.parametrize("experience", [None, "Senior", "not-a-range"])
    def test_unparsable_experience_is_never_a_rejection(
        self, experience: str | None
    ) -> None:
        assert filter_check(REQUEST, self.make(experience_level=experience)).accepted


class TestRegistration:
    def test_registers_under_the_default_agent_name(self) -> None:
        registry = HandlerRegistry()

        agent = register_discovery_handler(
            registry,
            searxng=FakeSearXNGClient(),
            firecrawl=FakeFirecrawlClient(),
        )

        assert isinstance(agent, DiscoveryAgent)
        assert registry.names() == frozenset({DISCOVERY_AGENT_NAME})
        assert registry.get(DISCOVERY_AGENT_NAME) == agent.handle

    def test_supports_a_custom_agent_name_and_replaces_by_name(self) -> None:
        registry = HandlerRegistry()

        register_discovery_handler(
            registry,
            searxng=FakeSearXNGClient(),
            firecrawl=FakeFirecrawlClient(),
            agent_name="custom_discovery",
        )
        register_discovery_handler(
            registry,
            searxng=FakeSearXNGClient(),
            firecrawl=FakeFirecrawlClient(),
            agent_name="custom_discovery",
        )

        assert registry.names() == frozenset({"custom_discovery"})

    def test_agent_construction_validates_its_limits(self) -> None:
        with pytest.raises(ValueError):
            make_agent(max_candidates=0)
        with pytest.raises(ValueError):
            make_agent(max_queries=0)
        with pytest.raises(ValueError):
            make_agent(source="")


class TestDiscoveryWithDatabase:
    def test_happy_path_persists_normalized_jobs_and_company(
        self, session
    ) -> None:
        agent, searxng, firecrawl = make_agent(HAPPY_RESULTS, HAPPY_PAGES)

        output = agent.discover(session, REQUEST)

        assert output["query"] == {
            "role": "AI Engineer",
            "location": "Bangalore",
            "experience": "1-3 years",
        }
        assert output["searched"] == 4
        assert output["candidates_found"] == 2
        assert output["jobs_created"] == 2
        assert output["jobs_updated"] == 0
        assert output["jobs_skipped"] == 0
        assert output["errors"] == []
        assert len(output["job_ids"]) == 2
        assert_invariant(output)
        assert json.dumps(output, allow_nan=False)

        assert output["queries"] == [
            "AI Engineer Bangalore jobs 1-3 years",
            "Artificial Intelligence Engineer Bangalore jobs 1-3 years",
        ]
        assert searxng.queries == output["queries"]
        assert firecrawl.requested == [URL_A, URL_B]

        job_a = get_job(session, URL_A)
        assert job_a.title == "Senior AI Engineer"
        assert job_a.location == "Bangalore"
        assert job_a.employment_type == "Full-time"
        assert job_a.experience_level == "1-3 years"
        assert job_a.description == "Own the agent platform."
        assert job_a.job_url == URL_A
        assert job_a.source == "searxng"
        assert job_a.is_active is True
        assert job_a.crawl_status == CrawlStatus.scraped
        assert job_a.crawled_at == T1
        assert job_a.first_seen_at == T1
        assert job_a.last_seen_at == T1

        assert job_a.company_id is not None
        company = session.get(Company, job_a.company_id)
        assert company is not None
        assert company.name == "Acme Inc."
        assert company.normalized_name == "acme"

        job_b = get_job(session, URL_B)
        assert job_b.title == "ML Engineer"
        assert job_b.company_id is None

        assert output["job_ids"] == [str(job_a.id), str(job_b.id)]

    def test_second_run_updates_rows_instead_of_duplicating(self, session) -> None:
        first, _, _ = make_agent(HAPPY_RESULTS, HAPPY_PAGES, clock=lambda: T1)
        first.discover(session, REQUEST)
        session.flush()
        first_ids = {
            job.id
            for job in session.scalars(
                select(Job).where(Job.canonical_url.in_([URL_A, URL_B]))
            )
        }

        second, _, _ = make_agent(HAPPY_RESULTS, HAPPY_PAGES, clock=lambda: T2)
        output = second.discover(session, REQUEST)
        session.expire_all()

        assert output["jobs_created"] == 0
        assert output["jobs_updated"] == 2
        assert output["errors"] == []
        assert_invariant(output)

        rows = session.scalars(
            select(Job).where(Job.canonical_url.in_([URL_A, URL_B]))
        ).all()
        assert {row.id for row in rows} == first_ids
        companies = session.scalars(
            select(Company).where(Company.normalized_name == "acme")
        ).all()
        assert len(companies) == 1
        for row in rows:
            assert row.first_seen_at == T1
            assert row.last_seen_at == T2
            assert row.crawled_at == T2

    def test_failed_crawl_is_recorded_without_faking_success(
        self, session
    ) -> None:
        results = [search_result(URL_A, "AI Engineer", "Build agents.")]
        pages = {URL_A: crawl_result(URL_A, markdown="", metadata={})}
        agent, _, _ = make_agent(results, pages)

        output = agent.discover(session, REQUEST)

        assert output["jobs_created"] == 1
        assert len(output["errors"]) == 1
        assert "no usable content" in output["errors"][0]
        job = get_job(session, URL_A)
        assert job.crawl_status == CrawlStatus.failed
        assert job.crawled_at is None
        assert job.title == "AI Engineer"
        assert job.description == "Build agents."

    def test_failed_recrawl_preserves_the_last_success_timestamp(
        self, session
    ) -> None:
        first, _, _ = make_agent(HAPPY_RESULTS, HAPPY_PAGES, clock=lambda: T1)
        first.discover(session, REQUEST)

        broken = dict(HAPPY_PAGES)
        broken[URL_A] = crawl_result(URL_A, markdown="", metadata={})
        second, _, _ = make_agent(
            [HAPPY_RESULTS[0]], broken, clock=lambda: T2
        )
        output = second.discover(session, REQUEST)
        session.expire_all()

        assert output["jobs_created"] == 0
        assert output["jobs_updated"] == 1
        assert output["errors"]
        job = get_job(session, URL_A)
        assert job.crawl_status == CrawlStatus.failed
        assert job.crawled_at == T1
        assert job.last_seen_at == T2

    def test_failed_recrawl_cannot_degrade_previously_crawled_content(
        self, session
    ) -> None:
        first, _, _ = make_agent(HAPPY_RESULTS, HAPPY_PAGES, clock=lambda: T1)
        first.discover(session, REQUEST)

        tracking_url = f"{URL_A}?utm_source=indeed"
        second, _, _ = make_agent(
            [search_result(tracking_url, "Indeed AI Engineer jobs", "SEO snippet")],
            {tracking_url: FirecrawlError("boom", kind="network_error")},
            clock=lambda: T2,
        )
        output = second.discover(session, REQUEST)
        session.expire_all()

        assert output["jobs_created"] == 0
        assert output["jobs_updated"] == 1
        job = get_job(session, URL_A)
        assert job.title == "Senior AI Engineer"
        assert job.description == "Own the agent platform."
        assert job.job_url == URL_A
        assert job.location == "Bangalore"
        assert job.employment_type == "Full-time"
        assert job.availability is AvailabilityStatus.available
        assert job.crawled_at == T1
        assert job.crawl_status == CrawlStatus.failed
        assert job.last_seen_at == T2

    def test_successful_recrawl_refreshes_previously_crawled_content(
        self, session
    ) -> None:
        first, _, _ = make_agent(HAPPY_RESULTS, HAPPY_PAGES, clock=lambda: T1)
        first.discover(session, REQUEST)

        refreshed = {
            **HAPPY_PAGES,
            URL_A: listing_page(
                URL_A,
                title="Staff AI Engineer",
                description="Lead the agent platform.",
                location="Bangalore",
                company="Acme Inc.",
                employment_type="Full-time",
                experience="3-5 years",
            ),
        }
        second, _, _ = make_agent(HAPPY_RESULTS, refreshed, clock=lambda: T2)
        output = second.discover(session, REQUEST)
        session.expire_all()

        assert output["jobs_created"] == 0
        assert output["jobs_updated"] == 2
        job = get_job(session, URL_A)
        assert job.title == "Staff AI Engineer"
        assert job.description == "Lead the agent platform."
        assert job.experience_level == "3-5 years"
        assert job.crawl_status == CrawlStatus.scraped
        assert job.crawled_at == T2

    def test_filter_rejections_are_counted_and_never_persisted(
        self, session
    ) -> None:
        results = [
            search_result(URL_A, "AI Engineer"),
            search_result(URL_B, "Data Scientist"),
        ]
        pages = {URL_A: listing_page(URL_A, title="AI Engineer")}
        agent, _, _ = make_agent(results, pages)

        output = agent.discover(session, REQUEST)

        assert output["candidates_found"] == 2
        assert output["jobs_created"] == 1
        assert output["jobs_skipped"] == 1
        assert_invariant(output)
        assert job_count(session, URL_A) == 1
        assert job_count(session, URL_B) == 0

    def test_malformed_urls_are_reported_but_are_not_candidates(
        self, session
    ) -> None:
        results = [
            search_result("javascript:void(0)", "AI Engineer"),
            search_result(URL_A, "AI Engineer"),
        ]
        pages = {URL_A: listing_page(URL_A, title="AI Engineer")}
        agent, _, _ = make_agent(results, pages)

        output = agent.discover(session, REQUEST)

        assert output["searched"] == 4
        assert output["candidates_found"] == 1
        assert output["jobs_skipped"] == 0
        assert any("malformed" in error for error in output["errors"])
        assert_invariant(output)
        assert job_count(session, URL_A) == 1

    def test_duplicate_canonical_urls_collapse_to_one_candidate(
        self, session
    ) -> None:
        results = [
            search_result(f"{URL_A}?utm_source=searxng", "AI Engineer"),
            search_result(f"{URL_A}/", "AI Engineer"),
        ]
        pages = {f"{URL_A}?utm_source=searxng": listing_page(URL_A, title="AI Engineer")}
        agent, _, _ = make_agent(results, pages)

        output = agent.discover(session, REQUEST)

        assert output["searched"] == 4
        assert output["candidates_found"] == 1
        assert output["jobs_created"] == 1
        assert job_count(session, URL_A) == 1

    def test_overlong_canonical_url_is_skipped_and_reported(
        self, session
    ) -> None:
        long_url = "https://example.com/jobs/" + "a" * 600
        results = [search_result(long_url, "AI Engineer")]
        agent, _, firecrawl = make_agent(results, {})

        output = agent.discover(session, REQUEST)

        assert output["candidates_found"] == 1
        assert output["jobs_created"] == 0
        assert output["jobs_skipped"] == 0
        assert output["invalid_results"] == 1
        assert any("longer than" in error for error in output["errors"])
        assert_invariant(output)
        assert firecrawl.requested == []
        assert job_count(session, canonicalize_url(long_url)) == 0

    def test_empty_search_returns_zero_counts(self, session) -> None:
        agent, _, _ = make_agent([], {})

        output = agent.discover(session, REQUEST)

        assert output == {
            "query": {
                "role": "AI Engineer",
                "location": "Bangalore",
                "experience": "1-3 years",
            },
            "queries": [
                "AI Engineer Bangalore jobs 1-3 years",
                "Artificial Intelligence Engineer Bangalore jobs 1-3 years",
            ],
            "searched": 0,
            "candidates_found": 0,
            "jobs_created": 0,
            "jobs_updated": 0,
            "jobs_skipped": 0,
            "errors": [],
            "job_ids": [],
            "duplicates_skipped": 0,
            "scrape_attempts": 0,
            "extractions_succeeded": 0,
            "listings_skipped": 0,
            "invalid_results": 0,
            "malformed_results": 0,
            "scrape_failures": 0,
            "issues": [],
        }

    def test_search_failure_propagates_before_any_database_work(
        self, session
    ) -> None:
        agent, _, _ = make_agent(error=SearXNGError("search down"))

        with pytest.raises(SearXNGError, match="search down"):
            agent.discover(session, REQUEST)

        assert job_count(session, URL_A) == 0
        assert job_count(session, URL_B) == 0

    def test_single_query_mode_runs_only_the_primary_query(self, session) -> None:
        agent, searxng, _ = make_agent(HAPPY_RESULTS, HAPPY_PAGES, max_queries=1)

        output = agent.discover(session, REQUEST)

        assert searxng.queries == ["AI Engineer Bangalore jobs 1-3 years"]
        assert output["queries"] == ["AI Engineer Bangalore jobs 1-3 years"]
        assert output["searched"] == 2
        assert output["jobs_created"] == 2

    def test_secondary_query_failure_is_recorded_and_the_run_continues(
        self, session
    ) -> None:
        searxng = FakeSearXNGClient(
            HAPPY_RESULTS, error=SearXNGError("boom"), fail_after=1
        )
        agent = DiscoveryAgent(
            searxng, FakeFirecrawlClient(HAPPY_PAGES), clock=lambda: T1
        )

        output = agent.discover(session, REQUEST)

        assert len(searxng.queries) == 2
        assert output["jobs_created"] == 2
        assert any("additional query" in error for error in output["errors"])
        assert_invariant(output)

    def test_company_id_survives_when_a_later_page_omits_the_company(
        self, session
    ) -> None:
        first, _, _ = make_agent(HAPPY_RESULTS, HAPPY_PAGES, clock=lambda: T1)
        first.discover(session, REQUEST)
        company_id = get_job(session, URL_A).company_id
        assert company_id is not None

        second, _, _ = make_agent(
            [HAPPY_RESULTS[0]],
            {URL_A: crawl_result(URL_A, markdown="# Senior AI Engineer")},
            clock=lambda: T2,
        )
        second.discover(session, REQUEST)
        session.expire_all()

        assert get_job(session, URL_A).company_id == company_id

    def test_metadata_only_page_counts_as_usable_content(self, session) -> None:
        results = [search_result(URL_A, "AI Engineer")]
        pages = {
            URL_A: crawl_result(
                URL_A, markdown=None, metadata={"title": "AI Engineer"}
            )
        }
        agent, _, _ = make_agent(results, pages)

        output = agent.discover(session, REQUEST)

        assert output["errors"] == []
        job = get_job(session, URL_A)
        assert job.crawl_status == CrawlStatus.scraped
        assert job.crawled_at == T1
        assert job.title == "AI Engineer"

    def test_max_candidates_caps_the_number_of_crawls(self, session) -> None:
        results = [
            search_result(f"https://example.com/{index}", "AI Engineer")
            for index in range(5)
        ]
        pages = {
            result.url: listing_page(result.url, title="AI Engineer")
            for result in results
        }
        agent, _, firecrawl = make_agent(results, pages, max_candidates=2)

        output = agent.discover(session, REQUEST)

        assert output["searched"] == 10
        assert output["candidates_found"] == 2
        assert len(firecrawl.requested) == 2
        assert_invariant(output)

    def test_custom_source_is_persisted(self, session) -> None:
        agent, _, _ = make_agent(
            HAPPY_RESULTS, HAPPY_PAGES, source="custom_crawler"
        )

        agent.discover(session, REQUEST)

        job = get_job(session, URL_A)
        assert job.source == "custom_crawler"


class TestHandleContract:
    def test_handle_rejects_invalid_payloads(self, session) -> None:
        agent, _, _ = make_agent(HAPPY_RESULTS, HAPPY_PAGES)
        context = run_context(session)

        with pytest.raises(DiscoveryInputError, match="must be a mapping"):
            agent.handle(None, context)
        with pytest.raises(DiscoveryInputError):
            agent.handle({"role": " ", "location": "BLR", "experience": "1-3"}, context)

    def test_handle_returns_the_structured_result(self, session) -> None:
        agent, searxng, _ = make_agent(HAPPY_RESULTS, HAPPY_PAGES)
        context = run_context(session)

        output = agent.handle(
            {
                "role": "  AI   Engineer ",
                "location": " Bangalore",
                "experience": "1-3 years",
            },
            context,
        )

        assert searxng.queries == [
            "AI Engineer Bangalore jobs 1-3 years",
            "Artificial Intelligence Engineer Bangalore jobs 1-3 years",
        ]
        assert output["jobs_created"] == 2
        assert_invariant(output)
        assert json.dumps(output, allow_nan=False)


def test_normalize_helpers_agree_with_persisted_keys() -> None:
    assert canonicalize_url(f"{URL_A}/?utm_source=x#apply") == URL_A
    assert normalize_company_name("Acme Inc.") == "acme"
