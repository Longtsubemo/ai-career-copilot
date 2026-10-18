"""Offline source-eligibility rules for career-page discovery."""

from __future__ import annotations

import json
import logging

import pytest

from app.agents.discovery.agent import (
    MAX_CANONICAL_URL_LENGTH,
    MAX_JOB_URL_LENGTH,
    DiscoveryAgent,
)
from app.agents.discovery.contract import DiscoveryRequest
from app.agents.discovery.query import build_search_queries
from app.agents.discovery.sources import (
    DEFAULT_SOURCE_REGISTRY,
    ReviewedCareerSource,
    SourceKind,
    SourceRegistry,
    assess_source_url,
)
from app.clients.searxng import SearchResult
from app.services.normalization import canonicalize_url
from tests.fake_clients import (
    FakeFirecrawlClient,
    FakeSearXNGClient,
    crawl_result,
)


POSTING_URLS = [
    (
        "greenhouse",
        "https://boards.greenhouse.io/acme/jobs/127817",
    ),
    (
        "lever",
        "https://jobs.lever.co/acme/5ac21346-8e0c-4494-8e7a-3eb92ff77902",
    ),
    (
        "ashby",
        "https://jobs.ashbyhq.com/acme/e9ed20fd-d45f-4aad-8a00-a19bfba0083e",
    ),
    (
        "smartrecruiters",
        "https://jobs.smartrecruiters.com/acme/74944235-experienced-seo-consultant-",
    ),
]


def test_known_ats_direct_posting_shapes_are_enabled() -> None:
    for provider, url in POSTING_URLS:
        assessment = assess_source_url(url)
        assert assessment.kind is SourceKind.DIRECT_JOB
        assert assessment.provider == provider
        assert assessment.eligible


def test_ats_roots_and_listing_paths_are_not_individual_postings() -> None:
    landing_urls = [
        "https://boards.greenhouse.io/acme",
        "https://jobs.lever.co/acme",
        "https://jobs.ashbyhq.com/acme",
        "https://jobs.smartrecruiters.com/acme",
    ]
    listing_urls = [
        "https://boards.greenhouse.io/acme/jobs",
        "https://jobs.lever.co/acme/jobs",
        "https://jobs.ashbyhq.com/acme/search",
        "https://jobs.smartrecruiters.com/acme/jobs",
    ]

    assert all(
        assess_source_url(url).kind is SourceKind.CAREER_LANDING
        for url in landing_urls
    )
    assert all(assess_source_url(url).kind is SourceKind.LISTING for url in listing_urls)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/jobs/view/1234567890",
        "https://www.naukri.com/jobs/123",
        "https://in.indeed.com/jobs/123",
        "https://example.com/jobs/ai-engineer-bangalore",
        "https://arbitrary.example/jobs/123",
        "https://boards.greenhouse.io.example.com/acme/jobs/123",
        "https://boards.greenhouse.io@evil.example/acme/jobs/123",
        "https://evil.example@boards.greenhouse.io/acme/jobs/123",
        "ftp://boards.greenhouse.io/acme/jobs/123",
        "https://boards.greenhouse.io:bad/acme/jobs/123",
        "https://boards.greenhouse.io/acme/jobs%2f127817",
    ],
)
def test_unsupported_deceptive_and_malformed_urls_are_ineligible(url: str) -> None:
    assert not assess_source_url(url).eligible


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/acme?gh_jid=127817", SourceKind.CAREER_LANDING),
        ("https://jobs.lever.co/acme?gh_jid=127817", SourceKind.CAREER_LANDING),
        ("https://jobs.ashbyhq.com/acme?vjk=private", SourceKind.CAREER_LANDING),
        ("https://arbitrary.example/jobs/123?gh_jid=127817", SourceKind.UNSUPPORTED_SOURCE),
    ],
)
def test_job_query_parameters_never_establish_source_or_posting_status(
    url: str, expected: SourceKind
) -> None:
    assessment = assess_source_url(url)
    assert assessment.kind is expected
    assert not assessment.eligible


def test_workday_requires_an_exact_registered_tenant_and_reviewed_path() -> None:
    unregistered = "https://acme.wd5.myworkdayjobs.com/reviewed-postings/REQ-12345"
    assert assess_source_url(unregistered).kind is SourceKind.UNSUPPORTED_SOURCE

    tenant = ReviewedCareerSource(
        name="Reviewed Acme Workday career site",
        hosts=frozenset({"acme.wd5.myworkdayjobs.com"}),
        direct_job_paths=(r"/reviewed-postings/REQ-[0-9]+",),
        career_landing_paths=(r"/en-US/Acme",),
        listing_paths=(r"/en-US/Acme/jobs",),
    )
    registry = SourceRegistry(workday_tenants=(tenant,))
    assert assess_source_url(unregistered, registry).kind is SourceKind.DIRECT_JOB
    other_tenant = unregistered.replace("acme.wd5", "other.wd5")
    assert assess_source_url(other_tenant, registry).kind is SourceKind.UNSUPPORTED_SOURCE


def test_employer_hosts_require_reviewed_exact_host_and_direct_path() -> None:
    employer = ReviewedCareerSource(
        name="Reviewed Acme careers",
        hosts=frozenset({"careers.acme.example"}),
        direct_job_paths=(r"/openings/REQ-[0-9]{5}",),
        career_landing_paths=(r"/careers",),
        listing_paths=(r"/openings",),
    )
    registry = SourceRegistry(employer_sources=(employer,))

    assert (
        assess_source_url("https://careers.acme.example/openings/REQ-12345", registry).kind
        is SourceKind.DIRECT_JOB
    )
    assert (
        assess_source_url("https://careers.acme.example/openings", registry).kind
        is SourceKind.LISTING
    )
    assert (
        assess_source_url("https://careers.acme.example/jobs/12345", registry).kind
        is SourceKind.UNKNOWN_URL
    )
    assert not assess_source_url(
        "https://jobs.acme.example/openings/REQ-12345", registry
    ).eligible
    request = DiscoveryRequest(
        role="AI Engineer", location="Bengaluru", experience="1-3 years"
    )
    assert all(
        "site:careers.acme.example" in query
        for query in build_search_queries(request, source_registry=registry)
    )


def test_queries_are_deterministic_bounded_and_hint_only_enabled_sources() -> None:
    request = DiscoveryRequest(
        role="AI Engineer", location="Bengaluru", experience="1-3 years"
    )
    queries = build_search_queries(request)

    assert queries == build_search_queries(request)
    assert len(queries) <= 2
    assert all("site:boards.greenhouse.io" in query for query in queries)
    assert all("site:jobs.lever.co" in query for query in queries)
    assert all("linkedin" not in query and "naukri" not in query for query in queries)
    assert DEFAULT_SOURCE_REGISTRY.search_hosts == (
        "boards.greenhouse.io",
        "jobs.ashbyhq.com",
        "jobs.lever.co",
        "jobs.smartrecruiters.com",
    )


def test_ineligible_results_do_not_consume_candidate_cap_or_reach_firecrawl() -> None:
    request = DiscoveryRequest(
        role="AI Engineer", location="Bengaluru", experience="1-3 years"
    )
    ineligible_urls = [
        "https://www.linkedin.com/jobs/view/1234567890",
        "https://boards.greenhouse.io/acme/jobs",
    ]
    eligible_urls = [POSTING_URLS[0][1], "https://boards.greenhouse.io/acme/jobs/127818"]
    results = [
        *(SearchResult(url, "AI Engineer") for url in ineligible_urls),
        *(SearchResult(url, "AI Engineer") for url in eligible_urls),
    ]
    pages = {
        url: crawl_result(
            url,
            markdown="# AI Engineer",
            metadata={
                "@type": "JobPosting",
                "title": "AI Engineer",
                    "description": (
                        "Design, build, and maintain production AI systems "
                        "while collaborating with product, research, and "
                        "engineering teams on reliable customer-facing "
                        "features and measurable improvements to deployed services."
                    ),
                "company": "Acme",
                "location": "Bengaluru",
            },
        )
        for url in eligible_urls
    }
    searxng = FakeSearXNGClient(results)
    firecrawl = FakeFirecrawlClient(pages)
    agent = DiscoveryAgent(
        searxng,
        firecrawl,
        max_candidates=1,
        max_queries=1,
    )
    # Keep this integration check database-free; persistence is outside the
    # source gate being exercised here.
    agent._persist = lambda session, plans, **kwargs: (0, 0, [])  # type: ignore[method-assign]

    output = agent.discover(None, request)  # type: ignore[arg-type]

    assert output["searched"] == 4
    assert output["candidates_found"] == 1
    assert output["scrape_attempts"] == 1
    assert firecrawl.requested == [eligible_urls[0]]
    assert all(issue["stage"] == "eligibility" for issue in output["issues"])
    assert {issue["url"] for issue in output["issues"]} == {
        "https://www.linkedin.com",
        "https://boards.greenhouse.io",
    }


def _oversized_greenhouse_url(limit: str = "canonical") -> str:
    if limit == "job_url":
        # The tracking query is removed from canonical_url but remains part of
        # job_url, so this exercises the separate persisted URL column limit.
        return (
            "https://boards.greenhouse.io/acme/jobs/127817?utm_source="
            "private-query-marker-"
            + ("x" * MAX_JOB_URL_LENGTH)
        )
    # This path matches the enabled Greenhouse direct-posting rule; only its
    # canonical URL length makes it ineligible for persistence and crawling.
    if limit != "canonical":
        raise ValueError(f"unknown URL length limit: {limit}")
    return (
        "https://boards.greenhouse.io/acme/jobs/"
        + ("7" * (MAX_CANONICAL_URL_LENGTH + 20))
        + "?access_token=private-query-marker#private-fragment-marker"
    )


def _discover_with_fake_persistence(results, pages, *, max_candidates, caplog):
    request = DiscoveryRequest(
        role="AI Engineer", location="Bengaluru", experience="1-3 years"
    )
    searxng = FakeSearXNGClient(results)
    firecrawl = FakeFirecrawlClient(pages)
    agent = DiscoveryAgent(
        searxng,
        firecrawl,
        max_candidates=max_candidates,
        max_queries=1,
    )
    agent._persist = lambda _session, plans, **kwargs: (
        len(plans),
        0,
        [f"job-{index}" for index, _plan in enumerate(plans)],
    )  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="app.agents.discovery.agent"):
        output = agent.discover(None, request)  # type: ignore[arg-type]
    return output, firecrawl


def _assert_candidate_counter_invariants(output: dict) -> None:
    assert output["candidates_found"] == (
        output["jobs_created"]
        + output["jobs_updated"]
        + output["jobs_skipped"]
        + output["listings_skipped"]
    )
    assert output["scrape_attempts"] == (
        output["extractions_succeeded"]
        + output["scrape_failures"]
        + output["page_validation_failures"]
    )


@pytest.mark.parametrize(
    "oversized_url",
    [_oversized_greenhouse_url(), _oversized_greenhouse_url("job_url")],
    ids=["canonical-url-limit", "job-url-limit"],
)
def test_oversized_source_eligible_url_does_not_consume_candidate_cap(
    oversized_url: str, caplog
) -> None:
    valid_url = "https://boards.greenhouse.io/acme/jobs/127819"
    assert assess_source_url(oversized_url).kind is SourceKind.DIRECT_JOB
    if "utm_source=" in oversized_url:
        assert len(oversized_url) > MAX_JOB_URL_LENGTH
        assert len(canonicalize_url(oversized_url)) <= MAX_CANONICAL_URL_LENGTH
    else:
        assert len(canonicalize_url(oversized_url)) > MAX_CANONICAL_URL_LENGTH

    page = crawl_result(
        valid_url,
        metadata={
            "title": "AI Engineer",
            "description": (
                "Design, build, and maintain production AI systems while "
                "collaborating with product, research, and engineering teams "
                "on reliable customer-facing features and measurable "
                "improvements to deployed services."
            ),
            "location": "Bengaluru",
        },
    )
    output, firecrawl = _discover_with_fake_persistence(
        [
            SearchResult(oversized_url, "AI Engineer"),
            SearchResult(valid_url, "AI Engineer"),
        ],
        {valid_url: page},
        max_candidates=1,
        caplog=caplog,
    )

    assert output["invalid_results"] == 1
    assert output["candidates_found"] == 1
    assert output["scrape_attempts"] == 1
    assert output["extractions_succeeded"] == 1
    assert output["scrape_failures"] == 0
    assert output["page_validation_failures"] == 0
    assert output["jobs_created"] == 1
    assert firecrawl.requested == [valid_url]
    invalid_issue = next(
        issue for issue in output["issues"] if issue["stage"] == "validate"
    )
    assert invalid_issue["url"] == "https://boards.greenhouse.io"
    assert invalid_issue["category"] == "invalid_url"
    assert "longer than" in invalid_issue["message"]
    _assert_candidate_counter_invariants(output)

    serialized = json.dumps(output)
    for sensitive in (
        "7" * (MAX_CANONICAL_URL_LENGTH + 20),
        "private-query-marker",
        "private-fragment-marker",
        "access_token",
        "utm_source",
    ):
        assert sensitive not in serialized
        assert sensitive not in caplog.text


@pytest.mark.parametrize(
    "oversized_url",
    [_oversized_greenhouse_url(), _oversized_greenhouse_url("job_url")],
    ids=["canonical-url-limit", "job-url-limit"],
)
def test_oversized_source_eligible_url_alone_is_rejected_before_crawl(
    oversized_url: str, caplog
) -> None:
    assert assess_source_url(oversized_url).kind is SourceKind.DIRECT_JOB
    if "utm_source=" in oversized_url:
        assert len(oversized_url) > MAX_JOB_URL_LENGTH
        assert len(canonicalize_url(oversized_url)) <= MAX_CANONICAL_URL_LENGTH
    else:
        assert len(canonicalize_url(oversized_url)) > MAX_CANONICAL_URL_LENGTH

    output, firecrawl = _discover_with_fake_persistence(
        [SearchResult(oversized_url, "AI Engineer")],
        {},
        max_candidates=1,
        caplog=caplog,
    )

    assert output["invalid_results"] == 1
    assert output["candidates_found"] == 0
    assert output["scrape_attempts"] == 0
    assert output["extractions_succeeded"] == 0
    assert output["scrape_failures"] == 0
    assert output["page_validation_failures"] == 0
    assert firecrawl.requested == []
    assert output["issues"][0]["url"] == "https://boards.greenhouse.io"
    assert output["issues"][0]["stage"] == "validate"
    assert output["issues"][0]["category"] == "invalid_url"
    _assert_candidate_counter_invariants(output)

    serialized = json.dumps(output)
    for sensitive in (
        "7" * (MAX_CANONICAL_URL_LENGTH + 20),
        "private-query-marker",
        "private-fragment-marker",
        "access_token",
        "utm_source",
    ):
        assert sensitive not in serialized
        assert sensitive not in caplog.text
