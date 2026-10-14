"""Offline tests for page-derived vacancy evidence in discovery."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.agents.discovery.agent import DiscoveryAgent
from app.agents.discovery.contract import DiscoveryRequest
from app.agents.discovery.extract import (
    PageEvidenceFailure,
    extract_page_evidence,
    validate_page_evidence,
)
from app.clients.firecrawl import FirecrawlError
from tests.fake_clients import (
    FakeFirecrawlClient,
    FakeSearXNGClient,
    TEST_DISCOVERY_SOURCE_REGISTRY,
    crawl_result,
    search_result,
)

URL = "https://boards.example.com/jobs/42"
REQUEST = DiscoveryRequest(
    role="AI Engineer", location="Bangalore", experience="1-3 years"
)
SUBSTANTIVE_DESCRIPTION = (
    "Design, build, and maintain production AI systems while collaborating "
    "with product, research, and engineering teams on reliable customer-facing "
    "features and measurable improvements to deployed services."
)


def make_agent(page: object, *, candidate_url: str = URL) -> DiscoveryAgent:
    return DiscoveryAgent(
        FakeSearXNGClient(
            [
                search_result(
                    candidate_url,
                    "AI Engineer with 1-3 years of experience",
                    "Build agentic systems in Bangalore. Apply now.",
                )
            ]
        ),
        FakeFirecrawlClient({candidate_url: page}),  # type: ignore[dict-item]
        source_registry=TEST_DISCOVERY_SOURCE_REGISTRY,
    )


def discover_without_database(
    page: object, *, candidate_url: str = URL
) -> tuple[dict, list, list]:
    agent = make_agent(page, candidate_url=candidate_url)
    captured: dict[str, list] = {"plans": [], "failed": []}

    def capture_persistence(
        _session: object, plans: list, *, failed_candidates: list | None = None
    ) -> tuple[int, int, list[str]]:
        captured["plans"] = list(plans)
        captured["failed"] = list(failed_candidates or ())
        return len(plans), 0, [f"planned-{index}" for index in range(len(plans))]

    agent._persist = capture_persistence  # type: ignore[method-assign]
    output = agent.discover(None, REQUEST)  # type: ignore[arg-type]
    return output, captured["plans"], captured["failed"]


def assert_attempt_invariant(output: dict) -> None:
    assert output["scrape_attempts"] == (
        output["extractions_succeeded"]
        + output["scrape_failures"]
        + output["page_validation_failures"]
    )
    assert output["candidates_found"] == (
        output["jobs_created"]
        + output["jobs_updated"]
        + output["jobs_skipped"]
        + output["listings_skipped"]
    )


def test_search_metadata_does_not_create_vacancy_after_failed_crawl() -> None:
    output, plans, failed = discover_without_database(
        FirecrawlError("generic failure", kind="network_error")
    )

    assert plans == []
    assert len(failed) == 1
    assert output["jobs_created"] == 0
    assert output["jobs_skipped"] == 1
    assert output["scrape_failures"] == 1
    assert output["page_validation_failures"] == 0
    assert output["job_ids"] == []
    assert_attempt_invariant(output)


def test_http_200_internal_error_reproduction_is_rejected_end_to_end() -> None:
    error_text = (
        "We are experiencing an internal error while loading this opportunity. "
        "Please refresh or contact support. The page could not be displayed at "
        "this time. We apologize for the inconvenience and appreciate your "
        "patience while our team investigates this issue."
    )
    candidate_url = f"{URL}?secret=private-token#fragment"
    page = crawl_result(
        candidate_url,
        markdown=None,
        metadata={
            "title": "AI Engineer",
            "description": error_text,
            "statusCode": 200,
        },
    )
    evidence = extract_page_evidence(page)
    validation = validate_page_evidence(page, evidence)

    assert validation.failure is PageEvidenceFailure.error

    agent = make_agent(page, candidate_url=candidate_url)
    captured: dict[str, list] = {"plans": [], "failed": []}

    def capture_persistence(
        _session: object, plans: list, *, failed_candidates: list | None = None
    ) -> tuple[int, int, list[str]]:
        captured["plans"] = list(plans)
        captured["failed"] = list(failed_candidates or ())
        return 0, 0, []

    agent._persist = capture_persistence  # type: ignore[method-assign]
    output = agent.discover(None, REQUEST)  # type: ignore[arg-type]

    assert captured["plans"] == []
    assert len(captured["failed"]) == 1
    assert output["jobs_created"] == 0
    assert output["scrape_attempts"] == 1
    assert output["extractions_succeeded"] == 0
    assert output["scrape_failures"] == 0
    assert output["page_validation_failures"] == 1
    issue = next(
        issue for issue in output["issues"]
        if issue["category"] == "validation_rejected"
    )
    assert issue["url"] == "https://boards.example.com"
    assert issue["message"] == (
        "page could not be validated as an individual vacancy"
    )
    assert "private-token" not in str(output)
    assert "fragment" not in str(output)
    assert "internal error" not in str(output)
    assert_attempt_invariant(output)


def test_generic_http_200_failure_page_with_job_title_is_rejected() -> None:
    page = crawl_result(
        URL,
        metadata={
            "title": "AI Engineer",
            "description": (
                "Something went wrong. We could not load this page. "
                "Please try again later or contact the site administrator "
                "if this problem continues."
            ),
            "statusCode": 200,
        },
    )
    validation = validate_page_evidence(page, extract_page_evidence(page))

    assert validation.failure is PageEvidenceFailure.error


def test_something_went_wrong_error_heading_is_rejected() -> None:
    page = crawl_result(
        URL,
        markdown=(
            "# AI Engineer\n\n## Something went wrong\n\n"
            "Please refresh this page or contact support for help."
        ),
        metadata={"statusCode": 200},
    )
    validation = validate_page_evidence(page, extract_page_evidence(page))

    assert validation.failure is PageEvidenceFailure.error


def test_http_200_not_found_and_hosting_error_templates_are_rejected() -> None:
    for description in (
        "Page not found. We could not display this content. Please try again "
        "later or contact the site administrator if the problem continues.",
        "The service is currently unavailable due to a temporary hosting "
        "failure. Please refresh the page later or contact support for help.",
    ):
        page = crawl_result(
            URL,
            metadata={
                "title": "AI Engineer",
                "description": description,
                "statusCode": 200,
            },
        )
        validation = validate_page_evidence(page, extract_page_evidence(page))

        assert validation.failure is PageEvidenceFailure.error


def test_http_200_challenge_content_is_rejected() -> None:
    page = crawl_result(
        URL,
        markdown="# AI Engineer\n\nAccess denied. Verify you are human.",
        metadata={"title": "AI Engineer", "statusCode": 200},
    )
    validation = validate_page_evidence(page, extract_page_evidence(page))

    assert validation.failure is PageEvidenceFailure.challenge


def test_empty_or_boilerplate_successful_crawls_are_not_vacancies() -> None:
    for page in (
        crawl_result(URL, markdown=None, metadata={}),
        crawl_result(
            URL,
            markdown="# Careers\n\nBrowse open roles and search our teams.",
            metadata={"title": "Careers"},
        ),
    ):
        output, plans, failed = discover_without_database(page)
        assert plans == []
        assert len(failed) == 1
        assert output["jobs_created"] == 0
        assert output["page_validation_failures"] == 1
        assert output["extractions_succeeded"] == 0
        assert_attempt_invariant(output)


def test_search_title_cannot_fill_missing_page_title() -> None:
    page = crawl_result(URL, markdown=SUBSTANTIVE_DESCRIPTION, metadata={})
    evidence = extract_page_evidence(page)
    validation = validate_page_evidence(page, evidence)

    assert evidence.title is None
    assert validation.failure is PageEvidenceFailure.insufficient

    output, plans, _ = discover_without_database(page)
    assert plans == []
    assert output["jobs_created"] == 0
    assert output["page_validation_failures"] == 1


def test_search_snippet_cannot_fill_missing_page_description() -> None:
    page = crawl_result(URL, markdown=None, metadata={"title": "AI Engineer"})
    evidence = extract_page_evidence(page)
    validation = validate_page_evidence(page, evidence)

    assert evidence.description is None
    assert validation.failure is PageEvidenceFailure.insufficient

    output, plans, _ = discover_without_database(page)
    assert plans == []
    assert output["jobs_created"] == 0
    assert output["page_validation_failures"] == 1


def test_career_boilerplate_is_not_a_vacancy_even_with_search_metadata() -> None:
    page = crawl_result(
        URL,
        markdown=(
            "Explore our career opportunities and join our talent community. "
            "Search our open positions, browse all jobs, and view all open "
            "positions across the company."
        ),
        metadata={"title": "Careers at Acme"},
    )
    output, plans, _ = discover_without_database(page)

    assert plans == []
    assert output["jobs_created"] == 0
    assert output["page_validation_failures"] == 1


def test_substantive_page_title_and_description_are_accepted() -> None:
    page = crawl_result(
        URL,
        markdown="# Senior AI Engineer\n\n" + SUBSTANTIVE_DESCRIPTION,
        metadata={"location": "Bangalore"},
    )
    validation = validate_page_evidence(page, extract_page_evidence(page))

    assert validation.accepted
    assert validation.extracted is not None
    assert validation.extracted.title == "Senior AI Engineer"
    assert validation.extracted.description == SUBSTANTIVE_DESCRIPTION
    assert validation.extracted.location == "Bangalore"


def test_responsibilities_and_requirements_provide_positive_job_evidence() -> None:
    page = crawl_result(
        URL,
        markdown=(
            "# AI Engineer\n\n## Responsibilities\n"
            "Design and build reliable machine learning services for our "
            "customers.\n\n## Requirements\n"
            "Develop production software and collaborate with data teams."
        ),
        metadata={},
    )
    validation = validate_page_evidence(page, extract_page_evidence(page))

    assert validation.accepted


def test_legitimate_job_content_can_mention_internal_tools_and_errors() -> None:
    description = (
        "Design and build internal tools for engineering teams, resolve "
        "production errors, troubleshoot incidents, improve error handling, "
        "monitor service health, and collaborate with colleagues to improve "
        "reliable systems used by customers every day."
    )
    page = crawl_result(
        URL,
        markdown=f"# AI Engineer\n\n{description}",
        metadata={},
    )
    validation = validate_page_evidence(page, extract_page_evidence(page))

    assert validation.accepted


def test_substantive_incident_response_description_is_accepted_for_planning() -> None:
    description = (
        "When something went wrong in production, investigate incidents, "
        "diagnose failures, and resolve root causes. Design and build "
        "monitoring systems, maintain alerting, and collaborate with "
        "engineering teams to improve reliability. Qualifications include "
        "experience operating production services and communicating clearly "
        "during incident reviews."
    )
    page = crawl_result(
        URL,
        markdown=f"# AI Engineer\n\n{description}",
        metadata={"statusCode": 200},
    )
    validation = validate_page_evidence(page, extract_page_evidence(page))

    assert validation.accepted
    assert validation.failure is not PageEvidenceFailure.error

    output, plans, failed = discover_without_database(page)

    assert len(plans) == 1
    assert plans[0].extracted.title == "AI Engineer"
    assert plans[0].extracted.description == description
    assert failed == []
    assert output["jobs_created"] == 1
    assert output["scrape_attempts"] == 1
    assert output["extractions_succeeded"] == 1
    assert output["scrape_failures"] == 0
    assert output["page_validation_failures"] == 0
    assert_attempt_invariant(output)
    assert output["candidates_found"] == (
        output["jobs_created"]
        + output["jobs_updated"]
        + output["jobs_skipped"]
        + output["listings_skipped"]
    )


def test_valid_job_posting_structured_data_is_used() -> None:
    page = crawl_result(
        URL,
        metadata={
            "jsonLd": {
                "@type": "JobPosting",
                "title": "AI Engineer",
                "description": SUBSTANTIVE_DESCRIPTION,
                "hiringOrganization": {"name": "Acme"},
                "jobLocation": {
                    "address": {
                        "addressLocality": "Bengaluru",
                        "addressCountry": "India",
                    }
                },
            }
        },
    )
    evidence = extract_page_evidence(page)
    validation = validate_page_evidence(page, evidence)

    assert evidence.structured_job_posting
    assert validation.accepted
    assert validation.extracted is not None
    assert validation.extracted.company_name == "Acme"
    assert validation.extracted.location == "Bengaluru, India"


def test_clear_error_surface_overrides_structured_job_posting() -> None:
    page = crawl_result(
        URL,
        markdown="# Application Error\n\nSomething went wrong while loading this page.",
        metadata={
            "jsonLd": {
                "@type": "JobPosting",
                "title": "AI Engineer",
                "description": SUBSTANTIVE_DESCRIPTION,
            }
        },
    )
    validation = validate_page_evidence(page, extract_page_evidence(page))

    assert validation.failure is PageEvidenceFailure.error


def test_listing_page_text_overrides_structured_posting_metadata() -> None:
    page = crawl_result(
        URL,
        markdown="# Browse all jobs\n\nSee all openings and search results.",
        metadata={
            "jsonLd": {
                "@type": "JobPosting",
                "title": "AI Engineer",
                "description": SUBSTANTIVE_DESCRIPTION,
            }
        },
    )
    validation = validate_page_evidence(page, extract_page_evidence(page))

    assert validation.failure is PageEvidenceFailure.listing


def test_successful_transport_does_not_accept_access_denied_page() -> None:
    page = crawl_result(
        URL,
        markdown="Access denied. Verify you are human before continuing.\n\n"
        + SUBSTANTIVE_DESCRIPTION,
        metadata={"title": "AI Engineer"},
    )
    output, plans, failed = discover_without_database(page)

    assert plans == []
    assert len(failed) == 1
    assert output["jobs_created"] == 0
    assert output["page_validation_failures"] == 1
    assert output["extractions_succeeded"] == 0
    failure_issue = next(
        issue for issue in output["issues"]
        if issue["category"] == "validation_rejected"
    )
    assert failure_issue["message"] == (
        "page could not be validated as an individual vacancy"
    )
    assert_attempt_invariant(output)


class _RecordingSession:
    def __init__(self, *, returned_id: object | None = None) -> None:
        self.statements = []
        self.returned_id = returned_id or uuid4()

    def execute(self, statement):
        self.statements.append(statement)
        return _RecordingResult(self.returned_id)


class _RecordingResult:
    def __init__(self, returned_id: object) -> None:
        self.returned_id = returned_id

    def first(self):
        return self.returned_id, False


def test_failed_attempt_update_cannot_change_verified_vacancy_fields() -> None:
    agent = make_agent(FirecrawlError("unused"))
    _, _, failed = discover_without_database(FirecrawlError("failed"))
    session = _RecordingSession()

    agent._persist(session, [], failed_candidates=failed)  # type: ignore[arg-type]

    statement = session.statements[0]
    assert statement.is_update
    assert {column.key for column in statement._values} == {
        "crawl_status",
        "last_seen_at",
        "updated_at",
    }
    compiled = str(statement.compile(dialect=postgresql.dialect())).lower()
    assert "update jobs" in compiled
    assert "title" not in compiled
    assert "description" not in compiled
    assert "crawled_at" not in compiled
    assert "availability" not in compiled


def test_rejected_error_page_uses_only_failed_recrawl_operational_update() -> None:
    error_text = (
        "We are experiencing an internal error while loading this opportunity. "
        "Please refresh or contact support. The page could not be displayed at "
        "this time. We apologize for the inconvenience and appreciate your "
        "patience while our team investigates this issue."
    )
    page = crawl_result(
        URL,
        metadata={"title": "AI Engineer", "description": error_text, "statusCode": 200},
    )
    agent = make_agent(page)
    _, plans, failed = discover_without_database(page)
    session = _RecordingSession()

    assert plans == []
    assert len(failed) == 1
    agent._persist(session, plans, failed_candidates=failed)  # type: ignore[arg-type]

    assert len(session.statements) == 1
    statement = session.statements[0]
    assert statement.is_update
    assert {column.key for column in statement._values} == {
        "crawl_status",
        "last_seen_at",
        "updated_at",
    }
    compiled = str(statement.compile(dialect=postgresql.dialect())).lower()
    assert "insert into jobs" not in compiled
    assert "title" not in compiled
    assert "description" not in compiled
    assert "crawled_at" not in compiled
    assert "availability" not in compiled


def test_successful_page_recrawl_upsert_uses_validated_page_fields() -> None:
    page = crawl_result(
        URL,
        markdown="# Page AI Engineer\n\n" + SUBSTANTIVE_DESCRIPTION,
        metadata={},
    )
    agent = make_agent(page)
    _, plans, _ = discover_without_database(page)
    session = _RecordingSession()

    agent._persist(session, plans)  # type: ignore[arg-type]

    statement = session.statements[0]
    compiled = str(statement.compile(dialect=postgresql.dialect())).lower()
    assert "insert into jobs" in compiled
    assert "on conflict (canonical_url) do update" in compiled
    assert "title = coalesce" in compiled
    assert "description = coalesce" in compiled
    assert plans[0].extracted.title == "Page AI Engineer"
    assert plans[0].extracted.description == SUBSTANTIVE_DESCRIPTION
