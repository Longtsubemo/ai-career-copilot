"""Behaviour of the Job Matching agent.

Contract and prompt tests are pure; persistence tests run against
PostgreSQL through the savepoint-backed ``session`` fixture. Gemini is
always a deterministic fake — the one test exercising the real client
injects a scripted SDK seam, so no test reaches Google's API.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agents.job_matching import (
    JOB_MATCHING_AGENT_NAME,
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    CandidateNotFoundError,
    InsufficientCandidateProfileError,
    InvalidResumeDocumentError,
    JobAnalysisNotFoundError,
    JobMatchingAgent,
    JobMatchingInputError,
    JobMatchingRequest,
    JobNotFoundError,
    MatchResult,
    MatchSignal,
    MatchVerdict,
    ResumeDocumentNotFoundError,
    build_matching_prompt,
    job_matching_idempotency_key,
    register_job_matching_handler,
)
from app.agents.job_matching.agent import persist_match
from app.agents.runtime import HandlerRegistry, RunContext
from app.clients.gemini import GeminiClient, GeminiError
from app.models.agent_run import AgentRun
from app.models.enums import DocumentKind, RunStatus
from app.models.job_analysis import JobAnalysis
from app.models.match import Match
from tests.fake_clients import FakeGeminiClient, RecordingGeminiApi
from tests.factories import make_candidate, make_company, make_document, make_job

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

MATCH_V2 = MatchResult(
    score=41.0,
    verdict=MatchVerdict.weak_match,
    matched_skills=["python"],
    experience_match=MatchSignal.mismatched,
    education_match=MatchSignal.unclear,
    location_match=MatchSignal.mismatched,
    seniority_match=MatchSignal.mismatched,
    rationale="Seniority and location expectations diverge.",
    confidence=0.6,
)

ANALYSIS_PAYLOAD = {
    "summary": "Backend role building agentic systems.",
    "confidence": 0.9,
    "required_skills": ["python", "postgresql"],
    "preferred_skills": ["kubernetes"],
    "responsibilities": ["design agent pipelines"],
    "qualifications": ["3+ years backend experience"],
    "experience_years_min": 3,
    "experience_years_max": 6,
    "seniority": "mid",
    "remote_type": "hybrid",
    "domain_keywords": ["artificial intelligence"],
    "technologies": ["python"],
    "education_requirements": ["bachelor's degree"],
    "certifications": [],
    "ambiguities": ["on-call expectations are unclear"],
}

DESCRIPTION_MARKER = "SECRET raw description marker"

PROMPT_ARGS = {
    "job_title": "Senior AI Engineer",
    "analysis": ANALYSIS_PAYLOAD,
    "candidate_name": "Ada Lovelace",
    "resume_kind": "master_resume",
    "resume_content": "Built agent pipelines in python.",
    "company_name": "Acme Inc.",
    "job_location": "Bangalore",
    "employment_type": "Full-time",
    "experience_level": "1-3 years",
    "headline": "Backend Engineer",
    "candidate_location": "Remote",
    "years_of_experience": 5.0,
    "seniority": "mid",
    "skills": ["python", "fastapi"],
    "summary": "Backend specialist.",
}


def add_analysis(
    session,
    job_id,
    *,
    prompt_version: str = "job-analysis-v1",
    status: RunStatus = RunStatus.succeeded,
    payload: dict | None = ANALYSIS_PAYLOAD,
    created_at: datetime | None = None,
) -> JobAnalysis:
    row = JobAnalysis(
        job_id=job_id,
        prompt_version=prompt_version,
        status=status,
        analysis=payload,
        model="gemini-3.8-flash",
    )
    if created_at is not None:
        row.created_at = created_at
    session.add(row)
    session.flush()
    return row


def build_world(session, *, with_analysis: bool = True) -> SimpleNamespace:
    company = make_company(session, name="Acme Inc.")
    job = make_job(
        session,
        company=company,
        title="Senior AI Engineer",
        description=DESCRIPTION_MARKER,
        location="Bangalore",
        employment_type="Full-time",
        experience_level="1-3 years",
    )
    candidate = make_candidate(session, name="Ada Lovelace")
    resume = make_document(
        session,
        candidate,
        DocumentKind.master_resume,
        "EXPERIENCE evidence: built agent pipelines in python.",
    )
    analysis = add_analysis(session, job.id) if with_analysis else None
    return SimpleNamespace(
        company=company,
        job=job,
        candidate=candidate,
        resume=resume,
        analysis=analysis,
    )


def match_count(session) -> int:
    return session.scalar(select(func.count()).select_from(Match))


def first_match(session) -> Match:
    row = session.scalar(select(Match).limit(1))
    assert row is not None
    return row


def base_payload() -> dict:
    return MATCH.model_dump(mode="json")


def run_context(session) -> RunContext:
    run = AgentRun(
        agent_name=JOB_MATCHING_AGENT_NAME,
        status=RunStatus.pending,
        idempotency_key="test:job_matching:context",
    )
    return RunContext(
        session=session, run=run, agent_name=JOB_MATCHING_AGENT_NAME, attempt=1
    )


class TestContract:
    def test_valid_request_accepts_string_and_native_uuids(self) -> None:
        native = uuid4()

        from_strings = JobMatchingRequest.from_payload(
            {
                "job_id": str(uuid4()),
                "candidate_id": str(uuid4()),
                "resume_document_id": str(uuid4()),
            }
        )
        from_native = JobMatchingRequest.from_payload(
            {
                "job_id": native,
                "candidate_id": native,
                "resume_document_id": native,
            }
        )

        assert isinstance(from_strings.job_id, type(native))
        assert from_native.resume_document_id == native

    @pytest.mark.parametrize(
        "payload",
        [None, "job_id", 7, ["job_id"]],
        ids=["none", "str", "int", "list"],
    )
    def test_request_rejects_non_mappings(self, payload: object) -> None:
        with pytest.raises(JobMatchingInputError, match="must be a mapping"):
            JobMatchingRequest.from_payload(payload)

    @pytest.mark.parametrize(
        "field", ["job_id", "candidate_id", "resume_document_id"]
    )
    def test_request_rejects_missing_field(self, field: str) -> None:
        payload = {
            "job_id": str(uuid4()),
            "candidate_id": str(uuid4()),
            "resume_document_id": str(uuid4()),
        }
        del payload[field]

        with pytest.raises(JobMatchingInputError):
            JobMatchingRequest.from_payload(payload)

    def test_request_rejects_malformed_uuid(self) -> None:
        payload = {
            "job_id": "not-a-uuid",
            "candidate_id": str(uuid4()),
            "resume_document_id": str(uuid4()),
        }

        with pytest.raises(JobMatchingInputError):
            JobMatchingRequest.from_payload(payload)

    def test_request_rejects_extra_field(self) -> None:
        payload = {
            "job_id": str(uuid4()),
            "candidate_id": str(uuid4()),
            "resume_document_id": str(uuid4()),
            "prompt_version": "job-matching-v1",
        }

        with pytest.raises(JobMatchingInputError):
            JobMatchingRequest.from_payload(payload)

    @pytest.mark.parametrize(
        "field", ["job_id", "candidate_id", "resume_document_id"]
    )
    def test_request_rejects_nil_uuids(self, field: str) -> None:
        payload = {
            "job_id": str(uuid4()),
            "candidate_id": str(uuid4()),
            "resume_document_id": str(uuid4()),
        }
        payload[field] = "00000000-0000-0000-0000-000000000000"

        with pytest.raises(JobMatchingInputError):
            JobMatchingRequest.from_payload(payload)

    def test_idempotency_key_is_deterministic_and_versioned(self) -> None:
        job_id, candidate_id, resume_id = uuid4(), uuid4(), uuid4()

        key = job_matching_idempotency_key(job_id, candidate_id, resume_id, "job-matching-v1")

        assert key == f"job_matching:{job_id}:{candidate_id}:{resume_id}:job-matching-v1"
        assert key == job_matching_idempotency_key(
            str(job_id), str(candidate_id), str(resume_id), "job-matching-v1"
        )
        assert (
            job_matching_idempotency_key(uuid4(), candidate_id, resume_id, "job-matching-v1")
            != key
        )
        assert (
            job_matching_idempotency_key(job_id, uuid4(), resume_id, "job-matching-v1")
            != key
        )
        assert (
            job_matching_idempotency_key(job_id, candidate_id, uuid4(), "job-matching-v1")
            != key
        )
        assert (
            job_matching_idempotency_key(job_id, candidate_id, resume_id, "job-matching-v2")
            != key
        )


class TestMatchResult:
    def test_result_round_trips_through_json(self) -> None:
        dumped = MATCH.model_dump(mode="json")

        assert json.dumps(dumped)
        assert MatchResult.model_validate(json.loads(json.dumps(dumped))) == MATCH

    @pytest.mark.parametrize("score", [0, 100, 0.0, 100.0])
    def test_score_boundaries_are_accepted(self, score: float) -> None:
        result = MatchResult.model_validate({**base_payload(), "score": score})

        assert result.score == float(score)

    @pytest.mark.parametrize("score", [-0.01, 100.01, -1.0, 101.0])
    def test_out_of_scale_scores_are_rejected(self, score: float) -> None:
        with pytest.raises(ValidationError):
            MatchResult.model_validate({**base_payload(), "score": score})

    def test_missing_score_is_rejected(self) -> None:
        payload = base_payload()
        del payload["score"]

        with pytest.raises(ValidationError):
            MatchResult.model_validate(payload)

    @pytest.mark.parametrize("verdict", [member.value for member in MatchVerdict])
    def test_verdict_vocabulary_is_accepted(self, verdict: str) -> None:
        result = MatchResult.model_validate({**base_payload(), "verdict": verdict})

        assert result.verdict == MatchVerdict(verdict)

    def test_free_form_verdict_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MatchResult.model_validate({**base_payload(), "verdict": "pretty good"})

    @pytest.mark.parametrize(
        "field",
        ["experience_match", "education_match", "location_match", "seniority_match"],
    )
    def test_signal_fields_accept_the_vocabulary(self, field: str) -> None:
        result = MatchResult.model_validate({**base_payload(), field: "unclear"})

        assert getattr(result, field) == MatchSignal.unclear

    @pytest.mark.parametrize(
        "field",
        ["experience_match", "education_match", "location_match", "seniority_match"],
    )
    def test_signal_fields_reject_bogus_values(self, field: str) -> None:
        with pytest.raises(ValidationError):
            MatchResult.model_validate({**base_payload(), field: "probably"})

    @pytest.mark.parametrize("confidence", [-0.1, 1.01])
    def test_confidence_out_of_bounds_is_rejected(self, confidence: float) -> None:
        with pytest.raises(ValidationError):
            MatchResult.model_validate({**base_payload(), "confidence": confidence})

    def test_extra_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MatchResult.model_validate({**base_payload(), "magic": True})

    def test_missing_rationale_is_rejected(self) -> None:
        payload = base_payload()
        del payload["rationale"]

        with pytest.raises(ValidationError):
            MatchResult.model_validate(payload)

    def test_blank_rationale_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MatchResult.model_validate({**base_payload(), "rationale": " "})


class TestPrompt:
    def test_prompt_version_is_the_agreed_v2(self) -> None:
        assert PROMPT_VERSION == "job-matching-v2"

    def test_prompt_contains_job_evidence(self) -> None:
        prompt = build_matching_prompt(**PROMPT_ARGS)

        assert prompt.index("JOB EVIDENCE") < prompt.index("CANDIDATE EVIDENCE")
        assert "Company: Acme Inc." in prompt
        assert "Title: Senior AI Engineer" in prompt
        assert "Location: Bangalore" in prompt
        assert "Employment type: Full-time" in prompt
        assert "Experience level: 1-3 years" in prompt
        assert "Structured job analysis:" in prompt
        assert (
            '"summary": "Backend role building agentic systems."' in prompt
        )
        assert '"postgresql"' in prompt
        assert '"ambiguities"' in prompt

    def test_prompt_contains_candidate_and_resume_evidence(self) -> None:
        prompt = build_matching_prompt(**PROMPT_ARGS)

        assert "Name: Ada Lovelace" in prompt
        assert "Headline: Backend Engineer" in prompt
        assert "Location: Remote" in prompt
        assert "Years of experience: 5\n" in prompt
        assert "Seniority: mid" in prompt
        assert "Skills: python, fastapi" in prompt
        assert "Profile summary:" in prompt
        assert "Backend specialist." in prompt
        assert "Resume document (master_resume):" in prompt
        assert "Built agent pipelines in python." in prompt

    def test_prompt_omits_absent_optional_fields(self) -> None:
        prompt = build_matching_prompt(
            job_title="AI Engineer",
            analysis=ANALYSIS_PAYLOAD,
            candidate_name="Ada Lovelace",
            resume_kind="master_resume",
            resume_content="Some resume.",
        )

        assert "Company:" not in prompt
        assert "Employment type:" not in prompt
        assert "Experience level:" not in prompt
        assert "Headline:" not in prompt
        assert "Years of experience:" not in prompt
        assert "Seniority:" not in prompt
        assert "Skills:" not in prompt
        assert "Profile summary:" not in prompt
        assert "Location:" not in prompt

    def test_system_instruction_demands_grounded_reasoning(self) -> None:
        assert "ONLY the supplied evidence" in SYSTEM_INSTRUCTION
        assert "required skills more heavily" in SYSTEM_INSTRUCTION
        assert "unclear" in SYSTEM_INSTRUCTION
        assert "Return only the JSON object" in SYSTEM_INSTRUCTION

    def test_system_instruction_distinguishes_missing_from_absent_evidence(self) -> None:
        instruction = " ".join(SYSTEM_INSTRUCTION.split())
        assert '"not evidenced"' in instruction
        assert "an omission means its status is unknown" in instruction
        assert (
            "only when the supplied candidate evidence explicitly says so"
            in instruction
        )

    def test_system_instruction_uses_the_job_stated_experience_requirement(self) -> None:
        instruction = " ".join(SYSTEM_INSTRUCTION.split())
        assert (
            "For a generic total-years requirement, compare total professional tenure."
            in instruction
        )
        assert "unrelated tenure does not satisfy it" in instruction
        assert (
            '"unclear" when dates or role relevance cannot be established'
            in instruction
        )

    def test_matching_output_contract_remains_unchanged(self) -> None:
        assert set(MatchResult.model_fields) == {
            "score",
            "verdict",
            "matched_skills",
            "missing_required_skills",
            "missing_preferred_skills",
            "strengths",
            "gaps",
            "experience_match",
            "education_match",
            "location_match",
            "seniority_match",
            "rationale",
            "confidence",
        }
        assert {value.value for value in MatchVerdict} == {
            "strong_match",
            "good_match",
            "partial_match",
            "weak_match",
            "poor_match",
        }
        assert {value.value for value in MatchSignal} == {
            "matched",
            "mismatched",
            "unclear",
        }


class TestRegistration:
    def test_registers_under_the_default_agent_name(self) -> None:
        registry = HandlerRegistry()

        agent = register_job_matching_handler(
            registry, gemini=FakeGeminiClient(MATCH)
        )

        assert isinstance(agent, JobMatchingAgent)
        assert agent.prompt_version == PROMPT_VERSION
        assert registry.names() == frozenset({JOB_MATCHING_AGENT_NAME})

    def test_supports_a_custom_name_and_version_and_replaces_by_name(self) -> None:
        registry = HandlerRegistry()

        first = register_job_matching_handler(
            registry,
            gemini=FakeGeminiClient(MATCH),
            agent_name="custom_matching",
            prompt_version="job-matching-v9",
        )
        register_job_matching_handler(
            registry,
            gemini=FakeGeminiClient(MATCH),
            agent_name="custom_matching",
            prompt_version="job-matching-v9",
        )

        assert first.prompt_version == "job-matching-v9"
        assert registry.names() == frozenset({"custom_matching"})

    def test_agent_construction_validates_the_prompt_version(self) -> None:
        with pytest.raises(ValueError, match="prompt_version"):
            JobMatchingAgent(FakeGeminiClient(MATCH), prompt_version="")
        with pytest.raises(ValueError, match="prompt_version"):
            JobMatchingAgent(FakeGeminiClient(MATCH), prompt_version="  ")


class TestAnalysisSelection:
    @pytest.mark.parametrize(
        "statuses",
        [
            [RunStatus.pending],
            [RunStatus.failed],
            [RunStatus.pending, RunStatus.failed],
        ],
        ids=["pending-only", "failed-only", "both"],
    )
    def test_non_succeeded_analyses_are_never_selected(
        self, session, world_factory, statuses: list[RunStatus]
    ) -> None:
        world = build_world(session, with_analysis=False)
        for index, status in enumerate(statuses):
            add_analysis(
                session,
                world.job.id,
                prompt_version=f"job-analysis-{index}",
                status=status,
            )
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)

        with pytest.raises(JobAnalysisNotFoundError):
            agent.match(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                resume_document_id=world.resume.id,
            )

        assert fake.calls == []
        assert match_count(session) == 0

    def test_succeeded_analysis_without_payload_is_not_selected(
        self, session
    ) -> None:
        world = build_world(session, with_analysis=False)
        add_analysis(session, world.job.id, payload=None)
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)

        with pytest.raises(JobAnalysisNotFoundError):
            agent.match(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                resume_document_id=world.resume.id,
            )

        assert fake.calls == []

    def test_newest_succeeded_analysis_is_selected_without_mixing(
        self, session
    ) -> None:
        world = build_world(session, with_analysis=False)
        base_time = datetime(2026, 1, 15, tzinfo=timezone.utc)
        older = add_analysis(
            session,
            world.job.id,
            prompt_version="job-analysis-v1",
            payload={**ANALYSIS_PAYLOAD, "summary": "Old analysis summary."},
            created_at=base_time,
        )
        newer = add_analysis(
            session,
            world.job.id,
            prompt_version="job-analysis-v2",
            created_at=base_time + timedelta(hours=1),
        )
        add_analysis(
            session,
            world.job.id,
            prompt_version="job-analysis-v3",
            status=RunStatus.failed,
            created_at=base_time + timedelta(hours=2),
        )
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)

        envelope = agent.match(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            resume_document_id=world.resume.id,
        )

        assert envelope["job_analysis_id"] == str(newer.id)
        assert envelope["job_analysis_id"] != str(older.id)
        row = first_match(session)
        assert row.job_analysis_id == newer.id

        prompt = fake.calls[0]["prompt"]
        assert "Backend role building agentic systems." in prompt
        assert "Old analysis summary." not in prompt


class TestPreconditions:
    def test_missing_job_raises_without_calling_gemini(self, session, world) -> None:
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)
        missing = uuid4()

        with pytest.raises(JobNotFoundError, match=str(missing)):
            agent.match(
                session,
                job_id=missing,
                candidate_id=world.candidate.id,
                resume_document_id=world.resume.id,
            )

        assert fake.calls == []
        assert match_count(session) == 0

    def test_missing_candidate_raises_without_calling_gemini(
        self, session, world
    ) -> None:
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)
        missing = uuid4()

        with pytest.raises(CandidateNotFoundError, match=str(missing)):
            agent.match(
                session,
                job_id=world.job.id,
                candidate_id=missing,
                resume_document_id=world.resume.id,
            )

        assert fake.calls == []
        assert match_count(session) == 0

    def test_missing_resume_raises_without_calling_gemini(
        self, session, world
    ) -> None:
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)
        missing = uuid4()

        with pytest.raises(ResumeDocumentNotFoundError, match=str(missing)):
            agent.match(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                resume_document_id=missing,
            )

        assert fake.calls == []
        assert match_count(session) == 0

    def test_resume_of_another_candidate_is_rejected(self, session, world) -> None:
        other = make_candidate(session)
        other_resume = make_document(
            session, other, DocumentKind.master_resume, "Someone else's resume."
        )
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)

        with pytest.raises(InvalidResumeDocumentError, match="does not belong"):
            agent.match(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                resume_document_id=other_resume.id,
            )

        assert fake.calls == []
        assert match_count(session) == 0

    def test_cover_letter_kind_is_rejected(self, session, world) -> None:
        letter = make_document(
            session,
            world.candidate,
            DocumentKind.cover_letter,
            "Dear team, I am writing to apply.",
        )
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)

        with pytest.raises(InvalidResumeDocumentError, match="kind"):
            agent.match(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                resume_document_id=letter.id,
            )

        assert fake.calls == []
        assert match_count(session) == 0

    def test_blank_resume_content_is_rejected(self, session, world) -> None:
        blank = make_document(session, world.candidate, DocumentKind.master_resume, "   ")
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)

        with pytest.raises(InvalidResumeDocumentError, match="no content"):
            agent.match(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                resume_document_id=blank.id,
            )

        assert fake.calls == []
        assert match_count(session) == 0

    def test_candidate_without_profile_evidence_is_rejected(
        self, session, world
    ) -> None:
        empty = make_candidate(
            session,
            headline=None,
            location=None,
            years_of_experience=None,
            seniority=None,
            skills=None,
            summary=None,
        )
        resume = make_document(
            session, empty, DocumentKind.master_resume, "A detailed resume body."
        )
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)

        with pytest.raises(InsufficientCandidateProfileError):
            agent.match(
                session,
                job_id=world.job.id,
                candidate_id=empty.id,
                resume_document_id=resume.id,
            )

        assert fake.calls == []
        assert match_count(session) == 0

    def test_missing_succeeded_analysis_is_rejected(self, session) -> None:
        world = build_world(session, with_analysis=False)
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)

        with pytest.raises(JobAnalysisNotFoundError, match=str(world.job.id)):
            agent.match(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                resume_document_id=world.resume.id,
            )

        assert fake.calls == []
        assert match_count(session) == 0


@pytest.fixture
def world(session) -> SimpleNamespace:
    return build_world(session)


@pytest.fixture
def world_factory():
    return build_world


class TestMatchingWithDatabase:
    def test_match_persists_a_structured_match(self, session, world) -> None:
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)

        envelope = agent.match(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            resume_document_id=world.resume.id,
        )

        assert envelope == {
            "job_id": str(world.job.id),
            "candidate_id": str(world.candidate.id),
            "resume_document_id": str(world.resume.id),
            "job_analysis_id": str(world.analysis.id),
            "prompt_version": PROMPT_VERSION,
            "model": "gemini-3.8-flash",
            "match": MATCH.model_dump(mode="json"),
        }
        assert match_count(session) == 1

        row = first_match(session)
        assert row.score == 72.5
        assert row.job_analysis_id == world.analysis.id
        assert row.model == "gemini-3.8-flash"
        assert row.prompt_version == PROMPT_VERSION
        assert row.verdict is not None
        assert row.verdict["verdict"] == "good_match"
        assert row.verdict["missing_required_skills"] == ["kubernetes"]
        assert row.verdict["confidence"] == 0.85
        assert "score" not in row.verdict

        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["schema"] is MatchResult
        assert call["system_instruction"] == SYSTEM_INSTRUCTION
        prompt = call["prompt"]
        assert "Company: Acme Inc." in prompt
        assert "Title: Senior AI Engineer" in prompt
        assert "Location: Bangalore" in prompt
        assert '"summary": "Backend role building agentic systems."' in prompt
        assert "Name: Ada Lovelace" in prompt
        assert "EXPERIENCE evidence: built agent pipelines in python." in prompt
        assert "Resume document (master_resume):" in prompt
        assert DESCRIPTION_MARKER not in prompt
        assert world.job.job_url not in prompt
        assert world.job.canonical_url not in prompt
        assert "searxng" not in prompt

    def test_repeated_matching_short_circuits_on_the_stored_match(
        self, session, world
    ) -> None:
        fake = FakeGeminiClient(MATCH)
        agent = JobMatchingAgent(fake)

        first = agent.match(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            resume_document_id=world.resume.id,
        )
        second = agent.match(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            resume_document_id=world.resume.id,
        )

        assert first == second
        assert len(fake.calls) == 1
        assert match_count(session) == 1

    def test_new_prompt_version_creates_new_historical_match(
        self, session, world
    ) -> None:
        v1 = JobMatchingAgent(FakeGeminiClient(MATCH))
        first_envelope = v1.match(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            resume_document_id=world.resume.id,
        )

        v3 = JobMatchingAgent(
            FakeGeminiClient(MATCH_V2, model="gemini-other"),
            prompt_version="job-matching-v3",
        )
        second_envelope = v3.match(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            resume_document_id=world.resume.id,
        )

        assert first_envelope["prompt_version"] == PROMPT_VERSION
        assert first_envelope["match"] == MATCH.model_dump(mode="json")
        assert second_envelope["prompt_version"] == "job-matching-v3"
        assert second_envelope["model"] == "gemini-other"
        assert second_envelope["match"] == MATCH_V2.model_dump(mode="json")
        assert match_count(session) == 2

        rows = session.scalars(
            select(Match).where(Match.job_id == world.job.id)
        ).all()
        by_version = {row.prompt_version: row for row in rows}
        assert set(by_version) == {PROMPT_VERSION, "job-matching-v3"}
        assert by_version[PROMPT_VERSION].score == 72.5
        assert by_version[PROMPT_VERSION].verdict is not None
        assert by_version[PROMPT_VERSION].verdict["verdict"] == "good_match"
        assert by_version["job-matching-v3"].score == 41.0
        assert by_version["job-matching-v3"].verdict is not None
        assert by_version["job-matching-v3"].verdict["verdict"] == "weak_match"

    def test_conflicting_persist_keeps_the_first_writer(self, session, world) -> None:
        first = MatchResult(
            score=61.0,
            verdict=MatchVerdict.partial_match,
            experience_match=MatchSignal.matched,
            education_match=MatchSignal.unclear,
            location_match=MatchSignal.unclear,
            seniority_match=MatchSignal.matched,
            rationale="First writer assessment.",
            confidence=0.5,
        )
        second = MatchResult(
            score=88.0,
            verdict=MatchVerdict.strong_match,
            experience_match=MatchSignal.matched,
            education_match=MatchSignal.matched,
            location_match=MatchSignal.matched,
            seniority_match=MatchSignal.matched,
            rationale="Second writer assessment.",
            confidence=0.9,
        )

        envelope_one = persist_match(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            resume_document_id=world.resume.id,
            job_analysis_id=world.analysis.id,
            result=first,
            model="model-a",
            prompt_version=PROMPT_VERSION,
        )
        envelope_two = persist_match(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            resume_document_id=world.resume.id,
            job_analysis_id=world.analysis.id,
            result=second,
            model="model-b",
            prompt_version=PROMPT_VERSION,
        )

        assert envelope_one["match"] == first.model_dump(mode="json")
        assert envelope_two == envelope_one
        assert envelope_two["model"] == "model-a"
        assert match_count(session) == 1

    def test_gemini_error_propagates_and_persists_nothing(
        self, session, world
    ) -> None:
        fake = FakeGeminiClient(GeminiError("quota exceeded"))
        agent = JobMatchingAgent(fake)

        with pytest.raises(GeminiError, match="quota exceeded"):
            agent.match(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                resume_document_id=world.resume.id,
            )

        assert match_count(session) == 0

    def test_out_of_scale_score_fails_without_persistence(self, session, world) -> None:
        payload = base_payload()
        payload["score"] = 150
        api = RecordingGeminiApi(parsed=None, text=json.dumps(payload))
        agent = JobMatchingAgent(GeminiClient("gemini-3.8-flash", api=api))

        with pytest.raises(GeminiError, match="does not match the expected schema"):
            agent.match(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                resume_document_id=world.resume.id,
            )

        assert len(api.calls) == 1
        assert match_count(session) == 0

    def test_malformed_structured_output_fails_without_persistence(
        self, session, world
    ) -> None:
        api = RecordingGeminiApi(parsed=None, text='{"score": 50}')
        agent = JobMatchingAgent(GeminiClient("gemini-3.8-flash", api=api))

        with pytest.raises(GeminiError, match="does not match the expected schema"):
            agent.match(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                resume_document_id=world.resume.id,
            )

        assert len(api.calls) == 1
        assert "JOB EVIDENCE" in api.calls[0]["contents"]
        assert match_count(session) == 0


class TestHandleContract:
    def test_handle_rejects_invalid_payloads(self, session) -> None:
        agent = JobMatchingAgent(FakeGeminiClient(MATCH))
        context = run_context(session)

        with pytest.raises(JobMatchingInputError, match="must be a mapping"):
            agent.handle(None, context)
        with pytest.raises(JobMatchingInputError):
            agent.handle({"job_id": "not-a-uuid"}, context)

    def test_handle_returns_the_structured_envelope(self, session, world) -> None:
        agent = JobMatchingAgent(FakeGeminiClient(MATCH))
        context = run_context(session)

        envelope = agent.handle(
            {
                "job_id": str(world.job.id),
                "candidate_id": str(world.candidate.id),
                "resume_document_id": str(world.resume.id),
            },
            context,
        )

        assert envelope["job_id"] == str(world.job.id)
        assert envelope["job_analysis_id"] == str(world.analysis.id)
        assert envelope["match"]["score"] == 72.5
        assert envelope["match"]["verdict"] == "good_match"
        assert match_count(session) == 1
