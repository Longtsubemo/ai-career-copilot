"""Cover Letter executed through the generic Agent Runtime.

Each test builds a fresh ``HandlerRegistry`` with an injected fake
Gemini client — the same wiring ``create_app()`` performs — so the full
create -> claim -> execute -> complete path runs with no network access
and no real credential.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.cover_letter import (
    COVER_LETTER_AGENT_NAME,
    PROMPT_VERSION,
    CoverLetterResult,
    cover_letter_idempotency_key,
    register_cover_letter_handler,
)
from app.agents.job_matching.contract import MatchResult, MatchSignal, MatchVerdict
from app.agents.runtime import AgentRuntime, HandlerRegistry, default_registry
from app.clients.gemini import GeminiError
from app.main import create_app
from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.document import Document
from app.models.enums import DocumentKind, RunStatus
from app.models.job import Job
from app.models.job_analysis import JobAnalysis
from app.models.match import Match
from tests.fake_clients import FakeGeminiClient
from tests.factories import make_candidate, make_document, make_job

LETTER = CoverLetterResult(
    content=(
        "Dear Hiring Manager,\n\n"
        "I am writing to apply for the Senior AI Engineer role.\n\n"
        "Over five years I have built backends and agent pipelines in python.\n\n"
        "I would welcome the chance to discuss the role.\n\n"
        "Kind regards,\nAda Lovelace"
    ),
    confidence=0.9,
)

LETTER_V2 = CoverLetterResult(
    content="Dear Team,\n\nA second, differently worded letter.",
    confidence=0.7,
)

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

RESUME_TEXT = "Built agent pipelines in python."


def add_analysis(session: Session, job_id) -> JobAnalysis:
    row = JobAnalysis(
        job_id=job_id,
        prompt_version="job-analysis-v1",
        status=RunStatus.succeeded,
        analysis=ANALYSIS_PAYLOAD,
        model="gemini-3.8-flash",
    )
    session.add(row)
    session.flush()
    return row


def add_match(
    session: Session, *, job, candidate, resume, analysis
) -> Match:
    detail = MATCH.model_dump(mode="json")
    score = detail.pop("score")
    row = Match(
        job_id=job.id,
        candidate_id=candidate.id,
        resume_document_id=resume.id,
        job_analysis_id=analysis.id,
        score=score,
        verdict=detail,
        model="gemini-3.8-flash",
        prompt_version="job-matching-v1",
    )
    session.add(row)
    session.flush()
    return row


def build_world(session: Session) -> SimpleNamespace:
    job = make_job(session, title="Senior AI Engineer")
    candidate = make_candidate(session, name="Ada Lovelace")
    resume = make_document(session, candidate, DocumentKind.master_resume, RESUME_TEXT)
    analysis = add_analysis(session, job.id)
    match = add_match(
        session, job=job, candidate=candidate, resume=resume, analysis=analysis
    )
    return SimpleNamespace(
        job=job,
        candidate=candidate,
        resume=resume,
        analysis=analysis,
        match=match,
    )


def build_registry(
    gemini: FakeGeminiClient, prompt_version: str = PROMPT_VERSION
) -> HandlerRegistry:
    registry = HandlerRegistry()
    register_cover_letter_handler(registry, gemini=gemini, prompt_version=prompt_version)
    return registry


def letter_payload(world: SimpleNamespace) -> dict:
    return {
        "job_id": str(world.job.id),
        "candidate_id": str(world.candidate.id),
        "master_resume_document_id": str(world.resume.id),
        "match_id": str(world.match.id),
    }


def letter_key(world: SimpleNamespace, prompt_version: str = PROMPT_VERSION) -> str:
    return cover_letter_idempotency_key(
        world.job.id,
        world.candidate.id,
        world.resume.id,
        world.match.id,
        prompt_version,
    )


def letter_count(session: Session) -> int:
    return session.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.kind == DocumentKind.cover_letter)
    )


def application_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Application))


@pytest.fixture
def pieces() -> tuple[HandlerRegistry, FakeGeminiClient]:
    registry = HandlerRegistry()
    gemini = FakeGeminiClient(LETTER)
    register_cover_letter_handler(registry, gemini=gemini)
    return registry, gemini


@pytest.fixture
def world(session: Session) -> SimpleNamespace:
    return build_world(session)


@pytest.fixture
def runtime(session: Session, pieces, world: SimpleNamespace) -> AgentRuntime:
    registry, _ = pieces
    return AgentRuntime(session, registry=registry)


def test_cover_letter_run_completes_and_stores_the_document(
    session: Session, runtime: AgentRuntime, pieces, world: SimpleNamespace
) -> None:
    _, gemini = pieces
    run = runtime.create_run(
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=letter_key(world),
        input=letter_payload(world),
        job_id=world.job.id,
        candidate_id=world.candidate.id,
    )

    claimed = runtime.claim_next(agent_name=COVER_LETTER_AGENT_NAME)
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
    assert output["master_resume_document_id"] == str(world.resume.id)
    assert output["match_id"] == str(world.match.id)
    assert output["prompt_version"] == PROMPT_VERSION
    assert output["model"] == "gemini-3.8-flash"
    assert output["cover_letter"] == LETTER.model_dump(mode="json")
    assert output["document_id"] is not None
    assert len(gemini.calls) == 1
    assert RESUME_TEXT in gemini.calls[0]["prompt"]
    assert "Match assessment:" in gemini.calls[0]["prompt"]

    assert letter_count(session) == 1
    row = session.scalar(
        select(Document).where(Document.kind == DocumentKind.cover_letter)
    )
    assert row is not None
    assert str(row.id) == output["document_id"]
    assert row.candidate_id == world.candidate.id
    assert row.job_id == world.job.id
    assert row.source_document_id == world.resume.id
    assert row.application_id is None
    assert row.agent_run_id == run.id
    assert row.content == LETTER.content
    assert row.model == "gemini-3.8-flash"
    assert row.prompt_version == PROMPT_VERSION
    assert run.output == output


def test_run_next_executes_cover_letter_in_one_step(
    session: Session, runtime: AgentRuntime, world: SimpleNamespace
) -> None:
    runtime.create_run(
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=letter_key(world),
        input=letter_payload(world),
        job_id=world.job.id,
        candidate_id=world.candidate.id,
    )

    finished = runtime.run_next(agent_name=COVER_LETTER_AGENT_NAME)

    assert finished is not None
    assert finished.status == RunStatus.succeeded
    assert finished.output is not None
    assert finished.output["cover_letter"] == LETTER.model_dump(mode="json")
    assert letter_count(session) == 1
    assert runtime.run_next(agent_name=COVER_LETTER_AGENT_NAME) is None


def test_duplicate_run_request_returns_the_existing_run(
    session: Session, runtime: AgentRuntime, world: SimpleNamespace
) -> None:
    key = letter_key(world)

    first = runtime.create_run(
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=key,
        input=letter_payload(world),
    )
    second = runtime.create_run(
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=key,
        input=letter_payload(world),
    )

    assert second.id == first.id
    assert session.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.idempotency_key == key)
    ) == 1


def test_repeated_request_after_success_reuses_the_stored_envelope(
    session: Session, runtime: AgentRuntime, pieces, world: SimpleNamespace
) -> None:
    _, gemini = pieces
    key = letter_key(world)

    runtime.create_run(
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=key,
        input=letter_payload(world),
    )
    finished = runtime.run_next(agent_name=COVER_LETTER_AGENT_NAME)
    assert finished is not None and finished.status == RunStatus.succeeded

    repeated = runtime.create_run(
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=key,
        input=letter_payload(world),
    )

    assert repeated.id == finished.id
    assert repeated.status == RunStatus.succeeded
    assert repeated.output == finished.output
    assert runtime.run_next(agent_name=COVER_LETTER_AGENT_NAME) is None
    assert len(gemini.calls) == 1
    assert letter_count(session) == 1


def test_invalid_payload_fails_the_run_deterministically(
    session: Session, pieces, world: SimpleNamespace
) -> None:
    registry, _ = pieces
    runtime = AgentRuntime(session, registry=registry)
    run = runtime.create_run(
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=letter_key(world),
        input={"job_id": "not-a-uuid"},
        max_attempts=1,
    )

    claimed = runtime.claim_next(agent_name=COVER_LETTER_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)

    assert run.status == RunStatus.failed
    assert run.error is not None
    assert "CoverLetterInputError" in run.error
    assert run.finished_at is not None
    assert letter_count(session) == 0


def test_missing_job_fails_after_retries_without_persisting(
    session: Session, pieces, world: SimpleNamespace
) -> None:
    registry, _ = pieces
    runtime = AgentRuntime(session, registry=registry)
    missing = uuid4()
    payload = {**letter_payload(world), "job_id": str(missing)}
    run = runtime.create_run(
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=cover_letter_idempotency_key(
            missing,
            world.candidate.id,
            world.resume.id,
            world.match.id,
            PROMPT_VERSION,
        ),
        input=payload,
    )

    for attempt in (1, 2):
        claimed = runtime.claim_next(agent_name=COVER_LETTER_AGENT_NAME)
        assert claimed is not None
        runtime.execute(claimed)
        assert run.status == RunStatus.pending
        assert run.error is not None
        assert "JobNotFoundError" in run.error
        assert run.attempt_count == attempt

    claimed = runtime.claim_next(agent_name=COVER_LETTER_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)

    assert run.status == RunStatus.failed
    assert run.attempt_count == 3
    assert run.finished_at is not None
    assert letter_count(session) == 0


def test_gemini_error_retries_then_fails_with_zero_documents(
    session: Session, world: SimpleNamespace
) -> None:
    registry = build_registry(FakeGeminiClient(GeminiError("quota exceeded")))
    runtime = AgentRuntime(session, registry=registry)
    runtime.create_run(
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=letter_key(world),
        input=letter_payload(world),
    )

    claimed = runtime.claim_next(agent_name=COVER_LETTER_AGENT_NAME)
    assert claimed is not None
    runtime.execute(claimed)
    assert claimed.status == RunStatus.pending
    assert claimed.error is not None
    assert "GeminiError" in claimed.error

    for _ in range(2):
        claimed = runtime.claim_next(agent_name=COVER_LETTER_AGENT_NAME)
        assert claimed is not None
        runtime.execute(claimed)

    assert claimed.status == RunStatus.failed
    assert claimed.attempt_count == 3
    assert claimed.finished_at is not None
    assert letter_count(session) == 0
    assert application_count(session) == 0


def test_new_prompt_version_creates_a_second_run_and_letter(
    session: Session, runtime: AgentRuntime, world: SimpleNamespace
) -> None:
    v1_run = runtime.create_run(
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=letter_key(world),
        input=letter_payload(world),
    )
    finished_v1 = runtime.run_next(agent_name=COVER_LETTER_AGENT_NAME)
    assert finished_v1 is not None and finished_v1.status == RunStatus.succeeded

    v2_runtime = AgentRuntime(
        session,
        registry=build_registry(
            FakeGeminiClient(LETTER_V2, model="gemini-other"),
            prompt_version="cover-letter-v2",
        ),
    )
    v2_run = v2_runtime.create_run(
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=letter_key(world, "cover-letter-v2"),
        input=letter_payload(world),
    )
    finished_v2 = v2_runtime.run_next(agent_name=COVER_LETTER_AGENT_NAME)

    assert finished_v2 is not None and finished_v2.status == RunStatus.succeeded
    assert v2_run.id != v1_run.id
    assert finished_v2.output is not None
    assert finished_v2.output["prompt_version"] == "cover-letter-v2"
    assert letter_count(session) == 2

    rows = session.scalars(
        select(Document).where(Document.kind == DocumentKind.cover_letter)
    ).all()
    by_version = {row.prompt_version: row for row in rows}
    assert set(by_version) == {PROMPT_VERSION, "cover-letter-v2"}
    assert by_version[PROMPT_VERSION].content == LETTER.content
    assert by_version[PROMPT_VERSION].source_document_id == world.resume.id
    assert by_version["cover-letter-v2"].content == LETTER_V2.content
    assert by_version["cover-letter-v2"].source_document_id == world.resume.id


def test_each_app_gets_its_own_registry_with_all_four_agents() -> None:
    app_one = create_app()
    app_two = create_app()

    registry_one = app_one.state.agent_registry
    registry_two = app_two.state.agent_registry
    expected = frozenset(
        {COVER_LETTER_AGENT_NAME, "job_discovery", "job_analysis", "job_matching"}
    )
    assert isinstance(registry_one, HandlerRegistry)
    assert registry_one is not registry_two
    assert registry_one.names() == expected
    assert registry_two.names() == expected
    assert registry_one.get(COVER_LETTER_AGENT_NAME) is not None
    assert COVER_LETTER_AGENT_NAME not in default_registry.names()


def test_generation_creates_no_application(
    session: Session, runtime: AgentRuntime, world: SimpleNamespace
) -> None:
    runtime.create_run(
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=letter_key(world),
        input=letter_payload(world),
    )

    finished = runtime.run_next(agent_name=COVER_LETTER_AGENT_NAME)

    assert finished is not None and finished.status == RunStatus.succeeded
    assert letter_count(session) == 1
    assert application_count(session) == 0
