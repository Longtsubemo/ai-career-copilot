"""Discovery executed through the generic Agent Runtime.

Each test builds a fresh ``HandlerRegistry`` with injected fake clients —
the same wiring ``create_app()`` performs — so the full
create -> claim -> execute -> complete path runs without any network
access. Committed rows only appear in the concurrency test, which cleans
up after itself.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.agents.discovery import (
    DISCOVERY_AGENT_NAME,
    DiscoveryAgent,
    DiscoveryRequest,
    discovery_idempotency_key,
    register_discovery_handler,
)
from app.agents.runtime import AgentRuntime, HandlerRegistry, default_registry
from app.clients.searxng import SearXNGError
from app.main import create_app
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.enums import RunStatus
from app.models.job import Job
from app.services.normalization import canonicalize_url, normalize_company_name
from tests.fake_clients import (
    FakeFirecrawlClient,
    FakeSearXNGClient,
    listing_page,
    search_result,
)
from tests.factories import unique_suffix

T1 = datetime(2026, 1, 15, tzinfo=timezone.utc)

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
        location="Bangalore",
        company="Acme Inc.",
    ),
    URL_B: listing_page(URL_B, title="ML Engineer", location="Bangalore"),
}


def build_registry(
    searxng: FakeSearXNGClient, firecrawl: FakeFirecrawlClient
) -> HandlerRegistry:
    registry = HandlerRegistry()
    register_discovery_handler(
        registry, searxng=searxng, firecrawl=firecrawl, clock=lambda: T1
    )
    return registry


@pytest.fixture
def pieces() -> tuple[HandlerRegistry, FakeSearXNGClient, FakeFirecrawlClient]:
    searxng = FakeSearXNGClient(HAPPY_RESULTS)
    firecrawl = FakeFirecrawlClient(HAPPY_PAGES)
    return build_registry(searxng, firecrawl), searxng, firecrawl


@pytest.fixture
def runtime(session: Session, pieces) -> AgentRuntime:
    registry, _, _ = pieces
    return AgentRuntime(session, registry=registry)


def create_discovery_run(
    runtime: AgentRuntime, *, input: dict | None = None, **overrides: object
) -> AgentRun:
    payload = input if input is not None else dict(request_payload())
    return runtime.create_run(
        agent_name=DISCOVERY_AGENT_NAME,
        idempotency_key=overrides.pop(
            "idempotency_key", f"job_discovery:test:{unique_suffix()}"
        ),
        input=payload,
        **overrides,
    )


def request_payload() -> dict[str, str]:
    return {
        "role": "AI Engineer",
        "location": "Bangalore",
        "experience": "1-3 years",
    }


def test_discovery_run_completes_and_stores_structured_output(
    session: Session, runtime: AgentRuntime, pieces
) -> None:
    _, searxng, _ = pieces
    run = create_discovery_run(
        runtime, idempotency_key=discovery_idempotency_key(REQUEST)
    )

    claimed = runtime.claim_next(agent_name=DISCOVERY_AGENT_NAME)
    assert claimed is not None
    assert claimed.id == run.id
    runtime.execute(claimed)

    assert claimed.status == RunStatus.succeeded
    assert claimed.error is None
    assert claimed.finished_at is not None
    output = claimed.output
    assert output is not None
    assert output["query"] == {
        "role": "AI Engineer",
        "location": "Bangalore",
        "experience": "1-3 years",
    }
    assert output["searched"] == 2
    assert output["jobs_created"] == 2
    assert output["jobs_updated"] == 0
    assert output["jobs_skipped"] == 0
    assert output["errors"] == []
    assert len(output["job_ids"]) == 2
    assert searxng.queries == ["AI Engineer Bangalore jobs 1-3 years"]

    persisted = session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.canonical_url.in_([URL_A, URL_B]))
    )
    assert persisted == 2
    assert run.output == output


def test_run_next_executes_discovery_in_one_step(
    session: Session, runtime: AgentRuntime
) -> None:
    create_discovery_run(runtime)

    finished = runtime.run_next(agent_name=DISCOVERY_AGENT_NAME)

    assert finished is not None
    assert finished.status == RunStatus.succeeded
    assert finished.output is not None
    assert finished.output["jobs_created"] == 2
    assert runtime.run_next(agent_name=DISCOVERY_AGENT_NAME) is None


def test_duplicate_discovery_request_returns_the_existing_run(
    session: Session, runtime: AgentRuntime
) -> None:
    key = discovery_idempotency_key(REQUEST)
    first = create_discovery_run(runtime, idempotency_key=key)
    second = create_discovery_run(runtime, idempotency_key=key)

    assert second.id == first.id
    assert session.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.idempotency_key == key)
    ) == 1


def test_invalid_payload_fails_the_run_deterministically(
    session: Session, runtime: AgentRuntime
) -> None:
    run = create_discovery_run(
        runtime,
        input={"role": " ", "location": "Bangalore", "experience": "1-3"},
        max_attempts=1,
    )

    claimed = runtime.claim_next(agent_name=DISCOVERY_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)

    assert run.status == RunStatus.failed
    assert run.error is not None
    assert "DiscoveryInputError" in run.error
    assert run.finished_at is not None


def test_search_failure_is_retried_until_attempts_are_exhausted(
    session: Session,
) -> None:
    failing = FakeSearXNGClient(error=SearXNGError("search down"))
    registry = build_registry(failing, FakeFirecrawlClient(HAPPY_PAGES))
    runtime = AgentRuntime(session, registry=registry)
    run = create_discovery_run(runtime)

    claimed = runtime.claim_next(agent_name=DISCOVERY_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)
    assert run.status == RunStatus.pending
    assert run.error is not None
    assert "SearXNGError" in run.error
    assert run.attempt_count == 1

    claimed = runtime.claim_next(agent_name=DISCOVERY_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)
    assert run.status == RunStatus.pending
    assert run.attempt_count == 2

    claimed = runtime.claim_next(agent_name=DISCOVERY_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)
    assert run.status == RunStatus.failed
    assert run.attempt_count == 3
    assert run.finished_at is not None

    assert runtime.claim_next(agent_name=DISCOVERY_AGENT_NAME) is None


def test_each_app_gets_its_own_registry_with_agents_registered() -> None:
    app_one = create_app()
    app_two = create_app()

    registry_one = app_one.state.agent_registry
    registry_two = app_two.state.agent_registry
    expected = frozenset(
        {DISCOVERY_AGENT_NAME, "job_analysis", "job_matching", "cover_letter"}
    )
    assert isinstance(registry_one, HandlerRegistry)
    assert registry_one is not registry_two
    assert registry_one.names() == expected
    assert registry_two.names() == expected
    assert registry_one.get(DISCOVERY_AGENT_NAME) is not None
    assert DISCOVERY_AGENT_NAME not in default_registry.names()


def test_concurrent_discovery_upserts_never_create_duplicates(
    engine: Engine,
) -> None:
    suffix = unique_suffix()
    url = f"https://example.com/jobs/concurrent-{suffix}"
    company_name = f"Concurrent Holdings {suffix}"
    canonical = canonicalize_url(url)
    normalized = normalize_company_name(company_name)
    results = [search_result(url, "AI Engineer", "Build agents.")]
    pages = {url: listing_page(url, title="AI Engineer", company=company_name)}

    def discover_once() -> dict:
        worker = Session(engine)
        try:
            agent = DiscoveryAgent(
                FakeSearXNGClient(results),
                FakeFirecrawlClient(pages),
                clock=lambda: T1,
            )
            output = agent.discover(worker, REQUEST)
            worker.commit()
            return output
        finally:
            worker.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(discover_once) for _ in range(2)]
            outputs = [future.result(timeout=30) for future in futures]

        assert sorted(output["jobs_created"] for output in outputs) == [0, 1]
        assert sorted(output["jobs_updated"] for output in outputs) == [0, 1]
        assert all(output["errors"] == [] for output in outputs)

        verifier = Session(engine)
        try:
            job_ids = verifier.scalars(
                select(Job.id).where(Job.canonical_url == canonical)
            ).all()
            assert len(job_ids) == 1
            company_ids = verifier.scalars(
                select(Company.id).where(Company.normalized_name == normalized)
            ).all()
            assert len(company_ids) == 1
            job = verifier.get(Job, job_ids[0])
            assert job is not None
            assert job.company_id == company_ids[0]
        finally:
            verifier.close()
    finally:
        cleanup = Session(engine)
        try:
            cleanup.execute(delete(Job).where(Job.canonical_url == canonical))
            cleanup.execute(
                delete(Company).where(Company.normalized_name == normalized)
            )
            cleanup.commit()
        finally:
            cleanup.close()
