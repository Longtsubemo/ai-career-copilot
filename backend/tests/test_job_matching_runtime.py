"""Job Matching executed through the generic Agent Runtime.

Each test builds a fresh ``HandlerRegistry`` with an injected fake
Gemini client — the same wiring ``create_app()`` performs — so the full
create -> claim -> execute -> complete path runs with no network access
and no real credential.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.agents.job_matching import (
    JOB_MATCHING_AGENT_NAME,
    PROMPT_VERSION,
    JobMatchingAgent,
    MatchResult,
    MatchSignal,
    MatchVerdict,
    job_matching_idempotency_key,
    register_job_matching_handler,
)
from app.agents.runtime import AgentRuntime, HandlerRegistry, default_registry
from app.clients.gemini import GeminiError
from app.main import create_app
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.document import Document
from app.models.enums import DocumentKind, RunStatus
from app.models.job import Job
from app.models.job_analysis import JobAnalysis
from app.models.match import Match
from tests.fake_clients import FakeGeminiClient
from tests.factories import make_candidate, make_document, make_job

MATCH = MatchResult(
    score=72.5,
    verdict=MatchVerdict.good_match,
    matched_skills=["python", "fastapi"],
    missing_required_skills=["kubernetes"],
    missing_preferred_skills=["terraform"],
    strengths=["five years building backends"],
    gaps=["no Kubernetes experience"],
    experience_match=MatchSignal.matched,
    education_match=MatchSignal.matched,
    location_match=MatchSignal.unclear,
    seniority_match=MatchSignal.matched,
    rationale="Strong backend overlap; two preferred tools absent.",
    confidence=0.85,
)

ANALYSIS_PAYLOAD = {
    "summary": "Backend role building agentic systems.",
    "confidence": 0.9,
    "required_skills": ["python", "postgresql"],
    "preferred_skills": ["kubernetes"],
    "experience_years_min": 3,
    "experience_years_max": 6,
    "seniority": "mid",
}


def add_analysis(session: Session, job_id, *, status: RunStatus = RunStatus.succeeded) -> JobAnalysis:
    row = JobAnalysis(
        job_id=job_id,
        prompt_version="job-analysis-v1",
        status=status,
        analysis=ANALYSIS_PAYLOAD,
        model="gemini-3.8-flash",
    )
    session.add(row)
    session.flush()
    return row


def build_world(session: Session) -> SimpleNamespace:
    job = make_job(session, title="Senior AI Engineer")
    candidate = make_candidate(session, name="Ada Lovelace")
    resume = make_document(
        session,
        candidate,
        DocumentKind.master_resume,
        "Built agent pipelines in python.",
    )
    analysis = add_analysis(session, job.id)
    return SimpleNamespace(
        job=job, candidate=candidate, resume=resume, analysis=analysis
    )


def build_registry(gemini: FakeGeminiClient) -> HandlerRegistry:
    registry = HandlerRegistry()
    register_job_matching_handler(registry, gemini=gemini)
    return registry


def match_payload(world: SimpleNamespace) -> dict:
    return {
        "job_id": str(world.job.id),
        "candidate_id": str(world.candidate.id),
        "resume_document_id": str(world.resume.id),
    }


def match_key(world: SimpleNamespace, prompt_version: str = PROMPT_VERSION) -> str:
    return job_matching_idempotency_key(
        world.job.id, world.candidate.id, world.resume.id, prompt_version
    )


def match_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Match))


@pytest.fixture
def pieces() -> tuple[HandlerRegistry, FakeGeminiClient]:
    registry = HandlerRegistry()
    gemini = FakeGeminiClient(MATCH)
    register_job_matching_handler(registry, gemini=gemini)
    return registry, gemini


@pytest.fixture
def world(session: Session) -> SimpleNamespace:
    return build_world(session)


@pytest.fixture
def runtime(session: Session, pieces, world: SimpleNamespace) -> AgentRuntime:
    registry, _ = pieces
    return AgentRuntime(session, registry=registry)


def test_job_matching_run_completes_and_stores_the_match(
    session: Session, runtime: AgentRuntime, pieces, world: SimpleNamespace
) -> None:
    _, gemini = pieces
    run = runtime.create_run(
        agent_name=JOB_MATCHING_AGENT_NAME,
        idempotency_key=match_key(world),
        input=match_payload(world),
        job_id=world.job.id,
        candidate_id=world.candidate.id,
    )

    claimed = runtime.claim_next(agent_name=JOB_MATCHING_AGENT_NAME)
    assert claimed is not None
    assert claimed.id == run.id
    runtime.execute(claimed)

    assert claimed.status == RunStatus.succeeded
    assert claimed.error is None
    assert claimed.finished_at is not None
    output = claimed.output
    assert output is not None
    assert output["job_id"] == str(world.job.id)
    assert output["candidate_id"] == str(world.candidate.id)
    assert output["resume_document_id"] == str(world.resume.id)
    assert output["job_analysis_id"] == str(world.analysis.id)
    assert output["prompt_version"] == PROMPT_VERSION
    assert output["model"] == "gemini-3.8-flash"
    assert output["match"] == MATCH.model_dump(mode="json")
    assert len(gemini.calls) == 1
    assert "Built agent pipelines in python." in gemini.calls[0]["prompt"]

    assert match_count(session) == 1
    row = session.scalar(select(Match).where(Match.job_id == world.job.id))
    assert row is not None
    assert float(row.score) == 72.5
    assert row.verdict == {
        key: value
        for key, value in MATCH.model_dump(mode="json").items()
        if key != "score"
    }
    assert run.output == output


def test_run_next_executes_job_matching_in_one_step(
    session: Session, runtime: AgentRuntime, world: SimpleNamespace
) -> None:
    runtime.create_run(
        agent_name=JOB_MATCHING_AGENT_NAME,
        idempotency_key=match_key(world),
        input=match_payload(world),
        job_id=world.job.id,
        candidate_id=world.candidate.id,
    )

    finished = runtime.run_next(agent_name=JOB_MATCHING_AGENT_NAME)

    assert finished is not None
    assert finished.status == RunStatus.succeeded
    assert finished.output is not None
    assert finished.output["match"] == MATCH.model_dump(mode="json")
    assert match_count(session) == 1
    assert runtime.run_next(agent_name=JOB_MATCHING_AGENT_NAME) is None


def test_duplicate_run_request_returns_the_existing_run(
    session: Session, runtime: AgentRuntime, world: SimpleNamespace
) -> None:
    key = match_key(world)

    first = runtime.create_run(
        agent_name=JOB_MATCHING_AGENT_NAME,
        idempotency_key=key,
        input=match_payload(world),
    )
    second = runtime.create_run(
        agent_name=JOB_MATCHING_AGENT_NAME,
        idempotency_key=key,
        input=match_payload(world),
    )

    assert second.id == first.id
    assert session.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.idempotency_key == key)
    ) == 1


def test_invalid_payload_fails_the_run_deterministically(
    session: Session, pieces, world: SimpleNamespace
) -> None:
    registry, _ = pieces
    runtime = AgentRuntime(session, registry=registry)
    run = runtime.create_run(
        agent_name=JOB_MATCHING_AGENT_NAME,
        idempotency_key=match_key(world),
        input={"job_id": "not-a-uuid"},
        max_attempts=1,
    )

    claimed = runtime.claim_next(agent_name=JOB_MATCHING_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)

    assert run.status == RunStatus.failed
    assert run.error is not None
    assert "JobMatchingInputError" in run.error
    assert run.finished_at is not None
    assert match_count(session) == 0


def test_missing_job_fails_after_retries_without_persisting(
    session: Session, pieces, world: SimpleNamespace
) -> None:
    registry, _ = pieces
    runtime = AgentRuntime(session, registry=registry)
    missing = uuid4()
    payload = {
        **match_payload(world),
        "job_id": str(missing),
    }
    run = runtime.create_run(
        agent_name=JOB_MATCHING_AGENT_NAME,
        idempotency_key=job_matching_idempotency_key(
            missing, world.candidate.id, world.resume.id, PROMPT_VERSION
        ),
        input=payload,
    )

    for attempt in (1, 2):
        claimed = runtime.claim_next(agent_name=JOB_MATCHING_AGENT_NAME)
        assert claimed is not None
        runtime.execute(claimed)
        assert run.status == RunStatus.pending
        assert run.error is not None
        assert "JobNotFoundError" in run.error
        assert run.attempt_count == attempt

    claimed = runtime.claim_next(agent_name=JOB_MATCHING_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)

    assert run.status == RunStatus.failed
    assert run.attempt_count == 3
    assert run.finished_at is not None
    assert match_count(session) == 0


def test_gemini_error_retries_then_fails_with_zero_match_rows(
    session: Session, world: SimpleNamespace
) -> None:
    registry = build_registry(FakeGeminiClient(GeminiError("quota exceeded")))
    runtime = AgentRuntime(session, registry=registry)
    runtime.create_run(
        agent_name=JOB_MATCHING_AGENT_NAME,
        idempotency_key=match_key(world),
        input=match_payload(world),
    )

    claimed = runtime.claim_next(agent_name=JOB_MATCHING_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)
    assert claimed.status == RunStatus.pending
    assert claimed.error is not None
    assert "GeminiError" in claimed.error

    for _ in range(2):
        claimed = runtime.claim_next(agent_name=JOB_MATCHING_AGENT_NAME)
        assert claimed is not None
        runtime.execute(claimed)

    assert claimed.status == RunStatus.failed
    assert claimed.attempt_count == 3
    assert claimed.finished_at is not None
    assert match_count(session) == 0


def test_new_prompt_version_creates_a_second_run_and_match_row(
    session: Session, runtime: AgentRuntime, world: SimpleNamespace
) -> None:
    v1_run = runtime.create_run(
        agent_name=JOB_MATCHING_AGENT_NAME,
        idempotency_key=match_key(world),
        input=match_payload(world),
    )
    finished_v1 = runtime.run_next(agent_name=JOB_MATCHING_AGENT_NAME)
    assert finished_v1 is not None and finished_v1.status == RunStatus.succeeded

    v2_registry = HandlerRegistry()
    register_job_matching_handler(
        v2_registry,
        gemini=FakeGeminiClient(MATCH),
        prompt_version="job-matching-v2",
    )
    v2_runtime = AgentRuntime(session, registry=v2_registry)
    v2_run = v2_runtime.create_run(
        agent_name=JOB_MATCHING_AGENT_NAME,
        idempotency_key=match_key(world, "job-matching-v2"),
        input=match_payload(world),
    )
    finished_v2 = v2_runtime.run_next(agent_name=JOB_MATCHING_AGENT_NAME)

    assert finished_v2 is not None and finished_v2.status == RunStatus.succeeded
    assert v2_run.id != v1_run.id
    assert finished_v2.output is not None
    assert finished_v2.output["prompt_version"] == "job-matching-v2"

    versions = set(
        session.scalars(
            select(Match.prompt_version).where(Match.job_id == world.job.id)
        ).all()
    )
    assert versions == {PROMPT_VERSION, "job-matching-v2"}
    assert match_count(session) == 2


def test_each_app_gets_its_own_registry_with_all_agents_registered() -> None:
    app_one = create_app()
    app_two = create_app()

    registry_one = app_one.state.agent_registry
    registry_two = app_two.state.agent_registry
    expected = frozenset(
        {JOB_MATCHING_AGENT_NAME, "job_discovery", "job_analysis", "cover_letter"}
    )
    assert isinstance(registry_one, HandlerRegistry)
    assert registry_one is not registry_two
    assert registry_one.names() == expected
    assert registry_two.names() == expected
    assert registry_one.get(JOB_MATCHING_AGENT_NAME) is not None
    assert JOB_MATCHING_AGENT_NAME not in default_registry.names()


def test_concurrent_matching_never_duplicates_the_match(engine: Engine) -> None:
    setup = Session(engine)
    try:
        world = build_world(setup)
        setup.commit()
        job_id, candidate_id, resume_id = (
            world.job.id,
            world.candidate.id,
            world.resume.id,
        )
    finally:
        setup.close()

    def match_once() -> dict:
        worker = Session(engine)
        try:
            agent = JobMatchingAgent(FakeGeminiClient(MATCH))
            output = agent.match(
                worker,
                job_id=job_id,
                candidate_id=candidate_id,
                resume_document_id=resume_id,
            )
            worker.commit()
            return output
        finally:
            worker.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(match_once) for _ in range(2)]
            outputs = [future.result(timeout=30) for future in futures]

        assert outputs[0] == outputs[1]
        assert outputs[0]["match"] == MATCH.model_dump(mode="json")

        verifier = Session(engine)
        try:
            rows = verifier.scalars(
                select(Match).where(Match.job_id == job_id)
            ).all()
            assert len(rows) == 1
            assert float(rows[0].score) == 72.5
            assert rows[0].prompt_version == PROMPT_VERSION
        finally:
            verifier.close()
    finally:
        cleanup = Session(engine)
        try:
            cleanup.execute(delete(Match).where(Match.job_id == job_id))
            cleanup.execute(delete(JobAnalysis).where(JobAnalysis.job_id == job_id))
            cleanup.execute(delete(Document).where(Document.id == resume_id))
            cleanup.execute(delete(Candidate).where(Candidate.id == candidate_id))
            cleanup.execute(delete(Job).where(Job.id == job_id))
            cleanup.commit()
        finally:
            cleanup.close()
