"""Job Analysis executed through the generic Agent Runtime.

Each test builds a fresh ``HandlerRegistry`` with an injected fake
Gemini client — the same wiring ``create_app()`` performs — so the full
create -> claim -> execute -> complete path runs with no network access
and no real credential.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.job_analysis import (
    JOB_ANALYSIS_AGENT_NAME,
    PROMPT_VERSION,
    JobAnalysisResult,
    job_analysis_idempotency_key,
    register_job_analysis_handler,
)
from app.agents.runtime import AgentRuntime, HandlerRegistry, default_registry
from app.clients.gemini import GeminiError
from app.main import create_app
from app.models.agent_run import AgentRun
from app.models.enums import RunStatus
from app.models.job_analysis import JobAnalysis
from tests.fake_clients import FakeGeminiClient
from tests.factories import make_job

ANALYSIS = JobAnalysisResult(
    summary="Senior AI engineer building agentic systems.",
    confidence=0.9,
    required_skills=["python", "fastapi"],
    experience_years_min=3,
    experience_years_max=6,
    seniority="senior",
)


def build_registry(gemini: FakeGeminiClient) -> HandlerRegistry:
    registry = HandlerRegistry()
    register_job_analysis_handler(registry, gemini=gemini)
    return registry


@pytest.fixture
def pieces() -> tuple[HandlerRegistry, FakeGeminiClient]:
    gemini = FakeGeminiClient(ANALYSIS)
    return build_registry(gemini), gemini


@pytest.fixture
def runtime(session: Session, pieces) -> AgentRuntime:
    registry, _ = pieces
    return AgentRuntime(session, registry=registry)


def analysis_key(job_id) -> str:
    return job_analysis_idempotency_key(job_id, PROMPT_VERSION)


def analysis_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(JobAnalysis))


def test_job_analysis_run_completes_and_stores_the_analysis(
    session: Session, runtime: AgentRuntime, pieces
) -> None:
    _, gemini = pieces
    job = make_job(session, title="Senior AI Engineer")
    run = runtime.create_run(
        agent_name=JOB_ANALYSIS_AGENT_NAME,
        idempotency_key=analysis_key(job.id),
        input={"job_id": str(job.id)},
        job_id=job.id,
    )

    claimed = runtime.claim_next(agent_name=JOB_ANALYSIS_AGENT_NAME)
    assert claimed is not None
    assert claimed.id == run.id
    runtime.execute(claimed)

    assert claimed.status == RunStatus.succeeded
    assert claimed.error is None
    assert claimed.finished_at is not None
    output = claimed.output
    assert output is not None
    assert output["job_id"] == str(job.id)
    assert output["prompt_version"] == PROMPT_VERSION
    assert output["model"] == "gemini-3.8-flash"
    assert output["analysis"] == ANALYSIS.model_dump(mode="json")
    assert len(gemini.calls) == 1
    assert "Title: Senior AI Engineer" in gemini.calls[0]["prompt"]

    assert analysis_count(session) == 1
    row = session.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job.id))
    assert row is not None
    assert row.status == RunStatus.succeeded
    assert run.output == output


def test_run_next_executes_job_analysis_in_one_step(
    session: Session, runtime: AgentRuntime
) -> None:
    job = make_job(session)
    runtime.create_run(
        agent_name=JOB_ANALYSIS_AGENT_NAME,
        idempotency_key=analysis_key(job.id),
        input={"job_id": str(job.id)},
        job_id=job.id,
    )

    finished = runtime.run_next(agent_name=JOB_ANALYSIS_AGENT_NAME)

    assert finished is not None
    assert finished.status == RunStatus.succeeded
    assert finished.output is not None
    assert finished.output["analysis"] == ANALYSIS.model_dump(mode="json")
    assert runtime.run_next(agent_name=JOB_ANALYSIS_AGENT_NAME) is None


def test_duplicate_run_request_returns_the_existing_run(
    session: Session, runtime: AgentRuntime
) -> None:
    job = make_job(session)
    key = analysis_key(job.id)

    first = runtime.create_run(
        agent_name=JOB_ANALYSIS_AGENT_NAME,
        idempotency_key=key,
        input={"job_id": str(job.id)},
    )
    second = runtime.create_run(
        agent_name=JOB_ANALYSIS_AGENT_NAME,
        idempotency_key=key,
        input={"job_id": str(job.id)},
    )

    assert second.id == first.id
    assert session.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.idempotency_key == key)
    ) == 1


def test_invalid_payload_fails_the_run_deterministically(
    session: Session, runtime: AgentRuntime
) -> None:
    job = make_job(session)
    run = runtime.create_run(
        agent_name=JOB_ANALYSIS_AGENT_NAME,
        idempotency_key=analysis_key(job.id),
        input={"job_id": "not-a-uuid"},
        max_attempts=1,
    )

    claimed = runtime.claim_next(agent_name=JOB_ANALYSIS_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)

    assert run.status == RunStatus.failed
    assert run.error is not None
    assert "JobAnalysisInputError" in run.error
    assert run.finished_at is not None
    assert analysis_count(session) == 0


def test_missing_job_fails_after_retries_without_persisting(
    session: Session,
) -> None:
    missing = uuid4()
    registry = build_registry(FakeGeminiClient(ANALYSIS))
    runtime = AgentRuntime(session, registry=registry)
    run = runtime.create_run(
        agent_name=JOB_ANALYSIS_AGENT_NAME,
        idempotency_key=job_analysis_idempotency_key(missing, PROMPT_VERSION),
        input={"job_id": str(missing)},
    )

    for attempt in (1, 2):
        claimed = runtime.claim_next(agent_name=JOB_ANALYSIS_AGENT_NAME)
        assert claimed is not None
        runtime.execute(claimed)
        assert run.status == RunStatus.pending
        assert run.error is not None
        assert "JobNotFoundError" in run.error
        assert run.attempt_count == attempt

    claimed = runtime.claim_next(agent_name=JOB_ANALYSIS_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)

    assert run.status == RunStatus.failed
    assert run.attempt_count == 3
    assert run.finished_at is not None
    assert analysis_count(session) == 0


def test_gemini_error_retries_then_fails_with_zero_analysis_rows(
    session: Session,
) -> None:
    registry = build_registry(
        FakeGeminiClient(GeminiError("quota exceeded"))
    )
    runtime = AgentRuntime(session, registry=registry)
    job = make_job(session)
    runtime.create_run(
        agent_name=JOB_ANALYSIS_AGENT_NAME,
        idempotency_key=analysis_key(job.id),
        input={"job_id": str(job.id)},
    )

    claimed = runtime.claim_next(agent_name=JOB_ANALYSIS_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)
    assert claimed.status == RunStatus.pending
    assert claimed.error is not None
    assert "GeminiError" in claimed.error

    for _ in range(2):
        claimed = runtime.claim_next(agent_name=JOB_ANALYSIS_AGENT_NAME)
        assert claimed is not None
        runtime.execute(claimed)

    assert claimed.status == RunStatus.failed
    assert claimed.attempt_count == 3
    assert claimed.finished_at is not None
    assert analysis_count(session) == 0


def test_new_prompt_version_creates_a_second_run_and_analysis_row(
    session: Session, runtime: AgentRuntime
) -> None:
    job = make_job(session)
    v1_run = runtime.create_run(
        agent_name=JOB_ANALYSIS_AGENT_NAME,
        idempotency_key=analysis_key(job.id),
        input={"job_id": str(job.id)},
    )
    finished_v1 = runtime.run_next(agent_name=JOB_ANALYSIS_AGENT_NAME)
    assert finished_v1 is not None and finished_v1.status == RunStatus.succeeded

    v2_registry = HandlerRegistry()
    register_job_analysis_handler(
        v2_registry,
        gemini=FakeGeminiClient(ANALYSIS),
        prompt_version="job-analysis-v2",
    )
    v2_runtime = AgentRuntime(session, registry=v2_registry)
    v2_run = v2_runtime.create_run(
        agent_name=JOB_ANALYSIS_AGENT_NAME,
        idempotency_key=job_analysis_idempotency_key(job.id, "job-analysis-v2"),
        input={"job_id": str(job.id)},
    )
    finished_v2 = v2_runtime.run_next(agent_name=JOB_ANALYSIS_AGENT_NAME)

    assert finished_v2 is not None and finished_v2.status == RunStatus.succeeded
    assert v2_run.id != v1_run.id
    assert finished_v2.output is not None
    assert finished_v2.output["prompt_version"] == "job-analysis-v2"
    assert finished_v2.output["analysis"] == ANALYSIS.model_dump(mode="json")

    versions = set(
        session.scalars(
            select(JobAnalysis.prompt_version).where(JobAnalysis.job_id == job.id)
        ).all()
    )
    assert versions == {PROMPT_VERSION, "job-analysis-v2"}


def test_each_app_gets_its_own_registry_with_all_agents_registered() -> None:
    app_one = create_app()
    app_two = create_app()

    registry_one = app_one.state.agent_registry
    registry_two = app_two.state.agent_registry
    expected = frozenset(
        {JOB_ANALYSIS_AGENT_NAME, "job_discovery", "job_matching", "cover_letter"}
    )
    assert isinstance(registry_one, HandlerRegistry)
    assert registry_one is not registry_two
    assert registry_one.names() == expected
    assert registry_two.names() == expected
    assert registry_one.get(JOB_ANALYSIS_AGENT_NAME) is not None
    assert JOB_ANALYSIS_AGENT_NAME not in default_registry.names()
