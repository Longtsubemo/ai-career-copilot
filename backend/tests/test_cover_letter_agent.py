"""Behaviour of the Cover Letter agent.

Contract, prompt and result-schema tests are pure; precondition and
persistence tests run against PostgreSQL through the savepoint-backed
``session`` fixture. Gemini is always a deterministic fake — the tests
exercising the real client inject a scripted SDK seam, so no test reaches
Google's API.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agents.cover_letter import (
    COVER_LETTER_AGENT_NAME,
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    CandidateNotFoundError,
    CoverLetterAgent,
    CoverLetterInputError,
    CoverLetterRequest,
    CoverLetterResult,
    InvalidMasterResumeError,
    InvalidMatchError,
    JobAnalysisNotFoundError,
    JobNotFoundError,
    MasterResumeNotFoundError,
    MatchNotFoundError,
    build_cover_letter_prompt,
    cover_letter_idempotency_key,
    persist_cover_letter,
    register_cover_letter_handler,
)
from app.agents.job_matching.contract import MatchResult, MatchSignal, MatchVerdict
from app.agents.runtime import HandlerRegistry, RunContext
from app.clients.gemini import GeminiClient, GeminiError
from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.document import Document
from app.models.enums import DocumentKind, RunStatus
from app.models.job_analysis import JobAnalysis
from app.models.match import Match
from tests.fake_clients import FakeGeminiClient, RecordingGeminiApi
from tests.factories import make_candidate, make_company, make_document, make_job

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

RESUME_TEXT = "EXPERIENCE evidence: built agent pipelines in python."

PROMPT_ARGS = {
    "job_title": "Senior AI Engineer",
    "analysis": ANALYSIS_PAYLOAD,
    "candidate_name": "Ada Lovelace",
    "match": MATCH,
    "resume_content": RESUME_TEXT,
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
    status: RunStatus = RunStatus.succeeded,
    payload: dict | None = ANALYSIS_PAYLOAD,
    prompt_version: str = "job-analysis-v1",
) -> JobAnalysis:
    row = JobAnalysis(
        job_id=job_id,
        prompt_version=prompt_version,
        status=status,
        analysis=payload,
        model="gemini-3.8-flash",
    )
    session.add(row)
    session.flush()
    return row


def add_match(
    session,
    *,
    job,
    candidate,
    resume,
    analysis,
    result: MatchResult = MATCH,
    prompt_version: str = "job-matching-v1",
) -> Match:
    detail = result.model_dump(mode="json")
    score = detail.pop("score")
    row = Match(
        job_id=job.id,
        candidate_id=candidate.id,
        resume_document_id=resume.id,
        job_analysis_id=analysis.id,
        score=score,
        verdict=detail,
        model="gemini-3.8-flash",
        prompt_version=prompt_version,
    )
    session.add(row)
    session.flush()
    return row


def build_world(session) -> SimpleNamespace:
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
    resume = make_document(session, candidate, DocumentKind.master_resume, RESUME_TEXT)
    analysis = add_analysis(session, job.id)
    match = add_match(
        session,
        job=job,
        candidate=candidate,
        resume=resume,
        analysis=analysis,
    )
    return SimpleNamespace(
        company=company,
        job=job,
        candidate=candidate,
        resume=resume,
        analysis=analysis,
        match=match,
    )


def letter_count(session) -> int:
    """Count agent-generated letters: fixtures never set a prompt version."""
    return session.scalar(
        select(func.count())
        .select_from(Document)
        .where(
            Document.kind == DocumentKind.cover_letter,
            Document.prompt_version.is_not(None),
        )
    )


def first_letter(session) -> Document:
    row = session.scalar(
        select(Document)
        .where(
            Document.kind == DocumentKind.cover_letter,
            Document.prompt_version.is_not(None),
        )
        .order_by(Document.created_at, Document.id)
        .limit(1)
    )
    assert row is not None
    return row


def application_count(session) -> int:
    return session.scalar(select(func.count()).select_from(Application))


def base_payload() -> dict:
    return LETTER.model_dump(mode="json")


def run_context(session) -> RunContext:
    run = AgentRun(
        agent_name=COVER_LETTER_AGENT_NAME,
        status=RunStatus.pending,
        idempotency_key="test:cover_letter:context",
    )
    session.add(run)
    session.flush()
    return RunContext(
        session=session, run=run, agent_name=COVER_LETTER_AGENT_NAME, attempt=1
    )


class TestContract:
    def test_valid_request_accepts_string_and_native_uuids(self) -> None:
        native = uuid4()

        from_strings = CoverLetterRequest.from_payload(
            {
                "job_id": str(uuid4()),
                "candidate_id": str(uuid4()),
                "master_resume_document_id": str(uuid4()),
                "match_id": str(uuid4()),
            }
        )
        from_native = CoverLetterRequest.from_payload(
            {
                "job_id": native,
                "candidate_id": native,
                "master_resume_document_id": native,
                "match_id": native,
            }
        )

        assert isinstance(from_strings.job_id, type(native))
        assert from_native.match_id == native

    @pytest.mark.parametrize(
        "payload",
        [None, "cover_letter", 7, ["job_id"]],
        ids=["none", "str", "int", "list"],
    )
    def test_request_rejects_non_mappings(self, payload: object) -> None:
        with pytest.raises(CoverLetterInputError, match="must be a mapping"):
            CoverLetterRequest.from_payload(payload)

    @pytest.mark.parametrize(
        "field",
        ["job_id", "candidate_id", "master_resume_document_id", "match_id"],
    )
    def test_request_rejects_missing_field(self, field: str) -> None:
        payload = {
            "job_id": str(uuid4()),
            "candidate_id": str(uuid4()),
            "master_resume_document_id": str(uuid4()),
            "match_id": str(uuid4()),
        }
        del payload[field]

        with pytest.raises(CoverLetterInputError):
            CoverLetterRequest.from_payload(payload)

    def test_request_rejects_malformed_uuid(self) -> None:
        payload = {
            "job_id": "not-a-uuid",
            "candidate_id": str(uuid4()),
            "master_resume_document_id": str(uuid4()),
            "match_id": str(uuid4()),
        }

        with pytest.raises(CoverLetterInputError):
            CoverLetterRequest.from_payload(payload)

    def test_request_rejects_extra_field(self) -> None:
        payload = {
            "job_id": str(uuid4()),
            "candidate_id": str(uuid4()),
            "master_resume_document_id": str(uuid4()),
            "match_id": str(uuid4()),
            "prompt_version": "cover-letter-v1",
        }

        with pytest.raises(CoverLetterInputError):
            CoverLetterRequest.from_payload(payload)

    @pytest.mark.parametrize(
        "field",
        ["job_id", "candidate_id", "master_resume_document_id", "match_id"],
    )
    def test_request_rejects_nil_uuids(self, field: str) -> None:
        payload = {
            "job_id": str(uuid4()),
            "candidate_id": str(uuid4()),
            "master_resume_document_id": str(uuid4()),
            "match_id": str(uuid4()),
        }
        payload[field] = "00000000-0000-0000-0000-000000000000"

        with pytest.raises(CoverLetterInputError):
            CoverLetterRequest.from_payload(payload)

    def test_idempotency_key_is_deterministic_and_versioned(self) -> None:
        job_id, candidate_id = uuid4(), uuid4()
        resume_id, match_id = uuid4(), uuid4()

        key = cover_letter_idempotency_key(
            job_id, candidate_id, resume_id, match_id, "cover-letter-v1"
        )

        assert key == (
            f"cover_letter:{job_id}:{candidate_id}:"
            f"{resume_id}:{match_id}:cover-letter-v1"
        )
        assert key == cover_letter_idempotency_key(
            str(job_id),
            str(candidate_id),
            str(resume_id),
            str(match_id),
            "cover-letter-v1",
        )
        assert (
            cover_letter_idempotency_key(
                uuid4(), candidate_id, resume_id, match_id, "cover-letter-v1"
            )
            != key
        )
        assert (
            cover_letter_idempotency_key(
                job_id, uuid4(), resume_id, match_id, "cover-letter-v1"
            )
            != key
        )
        assert (
            cover_letter_idempotency_key(
                job_id, candidate_id, uuid4(), match_id, "cover-letter-v1"
            )
            != key
        )
        assert (
            cover_letter_idempotency_key(
                job_id, candidate_id, resume_id, uuid4(), "cover-letter-v1"
            )
            != key
        )
        assert (
            cover_letter_idempotency_key(
                job_id, candidate_id, resume_id, match_id, "cover-letter-v2"
            )
            != key
        )


class TestCoverLetterResult:
    def test_result_round_trips_through_json(self) -> None:
        dumped = LETTER.model_dump(mode="json")

        assert json.dumps(dumped)
        assert CoverLetterResult.model_validate(json.loads(json.dumps(dumped))) == LETTER

    def test_content_is_stripped(self) -> None:
        result = CoverLetterResult.model_validate(
            {**base_payload(), "content": "  Dear team,\n\nBody.\n"}
        )

        assert result.content == "Dear team,\n\nBody."

    @pytest.mark.parametrize("content", ["", "   ", "\n\t "])
    def test_blank_content_is_rejected(self, content: str) -> None:
        with pytest.raises(ValidationError):
            CoverLetterResult.model_validate({**base_payload(), "content": content})

    def test_missing_content_is_rejected(self) -> None:
        payload = base_payload()
        del payload["content"]

        with pytest.raises(ValidationError):
            CoverLetterResult.model_validate(payload)

    @pytest.mark.parametrize("confidence", [0.0, 1.0, 0.5])
    def test_confidence_boundaries_are_accepted(self, confidence: float) -> None:
        result = CoverLetterResult.model_validate(
            {**base_payload(), "confidence": confidence}
        )

        assert result.confidence == confidence

    @pytest.mark.parametrize("confidence", [-0.01, 1.01, -1.0, 2.0])
    def test_out_of_bounds_confidence_is_rejected(self, confidence: float) -> None:
        with pytest.raises(ValidationError):
            CoverLetterResult.model_validate(
                {**base_payload(), "confidence": confidence}
            )

    def test_missing_confidence_is_rejected(self) -> None:
        payload = base_payload()
        del payload["confidence"]

        with pytest.raises(ValidationError):
            CoverLetterResult.model_validate(payload)

    def test_extra_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CoverLetterResult.model_validate({**base_payload(), "greeting": "Dear X"})


class TestPrompt:
    def test_prompt_version_is_the_agreed_v1(self) -> None:
        assert PROMPT_VERSION == "cover-letter-v1"

    def test_prompt_contains_job_evidence(self) -> None:
        prompt = build_cover_letter_prompt(**PROMPT_ARGS)

        assert prompt.index("JOB EVIDENCE") < prompt.index("CANDIDATE EVIDENCE")
        assert "Company: Acme Inc." in prompt
        assert "Title: Senior AI Engineer" in prompt
        assert "Location: Bangalore" in prompt
        assert "Employment type: Full-time" in prompt
        assert "Experience level: 1-3 years" in prompt
        assert "Structured job analysis:" in prompt
        assert '"summary": "Backend role building agentic systems."' in prompt
        assert '"postgresql"' in prompt
        assert '"ambiguities"' in prompt

    def test_prompt_contains_candidate_and_master_resume_evidence(self) -> None:
        prompt = build_cover_letter_prompt(**PROMPT_ARGS)

        assert "Name: Ada Lovelace" in prompt
        assert "Headline: Backend Engineer" in prompt
        assert "Location: Remote" in prompt
        assert "Years of experience: 5\n" in prompt
        assert "Seniority: mid" in prompt
        assert "Skills: python, fastapi" in prompt
        assert "Profile summary:" in prompt
        assert "Backend specialist." in prompt
        assert "Master resume:" in prompt
        assert RESUME_TEXT in prompt

    def test_prompt_contains_the_relevant_match_portions(self) -> None:
        prompt = build_cover_letter_prompt(**PROMPT_ARGS)

        assert "Match assessment:" in prompt
        assert "Fit score: 72.5" in prompt
        assert "Verdict: good_match" in prompt
        assert "Matched skills: python, fastapi" in prompt
        assert "Missing required skills: kubernetes" in prompt
        assert "Missing preferred skills: terraform" in prompt
        assert "Strengths:\n- five years building backends" in prompt
        assert "Gaps:\n- no Kubernetes experience" in prompt
        assert "Rationale: Strong backend overlap; two preferred tools absent." in prompt
        assert "Match confidence: 0.85" in prompt

    def test_prompt_renders_empty_match_lists_as_none(self) -> None:
        prompt = build_cover_letter_prompt(
            **{
                **PROMPT_ARGS,
                "match": MatchResult(
                    score=10.0,
                    verdict=MatchVerdict.poor_match,
                    experience_match=MatchSignal.unclear,
                    education_match=MatchSignal.unclear,
                    location_match=MatchSignal.unclear,
                    seniority_match=MatchSignal.unclear,
                    rationale="Almost no overlap.",
                    confidence=0.4,
                ),
            }
        )

        assert "Matched skills: none" in prompt
        assert "Missing required skills: none" in prompt
        assert "Missing preferred skills: none" in prompt
        assert "Strengths: none" in prompt
        assert "Gaps: none" in prompt

    def test_prompt_omits_absent_optional_fields(self) -> None:
        prompt = build_cover_letter_prompt(
            job_title="AI Engineer",
            analysis=ANALYSIS_PAYLOAD,
            candidate_name="Ada Lovelace",
            match=MATCH,
            resume_content=RESUME_TEXT,
        )

        assert "Company:" not in prompt
        assert "Employment type:" not in prompt
        assert "Experience level:" not in prompt
        assert "Headline:" not in prompt
        assert "Years of experience:" not in prompt
        assert "Seniority:" not in prompt
        assert "Skills:" not in prompt
        assert "Profile summary:" not in prompt

    def test_prompt_excludes_unrelated_and_internal_fields(self) -> None:
        prompt = build_cover_letter_prompt(**PROMPT_ARGS)

        assert DESCRIPTION_MARKER not in prompt
        assert "https://example.com/jobs/" not in prompt
        assert "searxng" not in prompt
        assert "experience_match" not in prompt
        assert "education_match" not in prompt
        assert "location_match" not in prompt
        assert "seniority_match" not in prompt
        assert "resume_document_id" not in prompt
        assert "job_analysis_id" not in prompt
        assert "application" not in prompt.lower()

    def test_system_instruction_demands_grounded_writing(self) -> None:
        assert "ONLY the supplied evidence" in SYSTEM_INSTRUCTION
        assert "Never fabricate" in SYSTEM_INSTRUCTION
        assert "missing required skill" in SYSTEM_INSTRUCTION
        assert "nice to have" in SYSTEM_INSTRUCTION
        assert "generic filler" in SYSTEM_INSTRUCTION
        assert "Return only the JSON object" in SYSTEM_INSTRUCTION


class TestRegistration:
    def test_registers_under_the_default_agent_name(self) -> None:
        registry = HandlerRegistry()

        agent = register_cover_letter_handler(
            registry, gemini=FakeGeminiClient(LETTER)
        )

        assert isinstance(agent, CoverLetterAgent)
        assert agent.prompt_version == PROMPT_VERSION
        assert registry.names() == frozenset({COVER_LETTER_AGENT_NAME})

    def test_supports_a_custom_name_and_version_and_replaces_by_name(self) -> None:
        registry = HandlerRegistry()

        first = register_cover_letter_handler(
            registry,
            gemini=FakeGeminiClient(LETTER),
            agent_name="custom_letter",
            prompt_version="cover-letter-v9",
        )
        register_cover_letter_handler(
            registry,
            gemini=FakeGeminiClient(LETTER),
            agent_name="custom_letter",
            prompt_version="cover-letter-v9",
        )

        assert first.prompt_version == "cover-letter-v9"
        assert registry.names() == frozenset({"custom_letter"})

    def test_agent_construction_validates_the_prompt_version(self) -> None:
        with pytest.raises(ValueError, match="prompt_version"):
            CoverLetterAgent(FakeGeminiClient(LETTER), prompt_version="")
        with pytest.raises(ValueError, match="prompt_version"):
            CoverLetterAgent(FakeGeminiClient(LETTER), prompt_version="  ")


@pytest.fixture
def world(session) -> SimpleNamespace:
    return build_world(session)


class TestPreconditions:
    def test_missing_job_raises_without_calling_gemini(self, session, world) -> None:
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)
        missing = uuid4()

        with pytest.raises(JobNotFoundError, match=str(missing)):
            agent.generate(
                session,
                job_id=missing,
                candidate_id=world.candidate.id,
                master_resume_document_id=world.resume.id,
                match_id=world.match.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_missing_candidate_raises_without_calling_gemini(
        self, session, world
    ) -> None:
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)
        missing = uuid4()

        with pytest.raises(CandidateNotFoundError, match=str(missing)):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=missing,
                master_resume_document_id=world.resume.id,
                match_id=world.match.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_missing_master_resume_raises_without_calling_gemini(
        self, session, world
    ) -> None:
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)
        missing = uuid4()

        with pytest.raises(MasterResumeNotFoundError, match=str(missing)):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=missing,
                match_id=world.match.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_resume_of_another_candidate_is_rejected(self, session, world) -> None:
        other = make_candidate(session)
        other_resume = make_document(
            session, other, DocumentKind.master_resume, "Someone else's resume."
        )
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)

        with pytest.raises(InvalidMasterResumeError, match="does not belong"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=other_resume.id,
                match_id=world.match.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_non_master_resume_kind_is_rejected(self, session, world) -> None:
        letter = make_document(
            session,
            world.candidate,
            DocumentKind.cover_letter,
            "Dear team, I am writing to apply.",
        )
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)

        with pytest.raises(InvalidMasterResumeError, match="kind"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=letter.id,
                match_id=world.match.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_blank_master_resume_content_is_rejected(self, session, world) -> None:
        blank = make_document(session, world.candidate, DocumentKind.master_resume, "  ")
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)

        with pytest.raises(InvalidMasterResumeError, match="no content"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=blank.id,
                match_id=world.match.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_missing_match_raises_without_calling_gemini(self, session, world) -> None:
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)
        missing = uuid4()

        with pytest.raises(MatchNotFoundError, match=str(missing)):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=world.resume.id,
                match_id=missing,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_match_of_another_job_is_rejected(self, session, world) -> None:
        other_company = make_company(session, name="Other Inc.")
        other_job = make_job(session, company=other_company, title="Data Engineer")
        other_analysis = add_analysis(session, other_job.id)
        other_match = add_match(
            session,
            job=other_job,
            candidate=world.candidate,
            resume=world.resume,
            analysis=other_analysis,
            prompt_version="job-matching-custom",
        )
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)

        with pytest.raises(InvalidMatchError, match="does not belong to job"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=world.resume.id,
                match_id=other_match.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_match_of_another_candidate_is_rejected(self, session, world) -> None:
        other = make_candidate(session, name="Grace Hopper")
        other_resume = make_document(
            session, other, DocumentKind.master_resume, "Another resume."
        )
        other_match = add_match(
            session,
            job=world.job,
            candidate=other,
            resume=other_resume,
            analysis=world.analysis,
        )
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)

        with pytest.raises(InvalidMatchError, match="does not belong to candidate"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=world.resume.id,
                match_id=other_match.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_match_computed_against_another_resume_is_rejected(
        self, session, world
    ) -> None:
        tailored = make_document(
            session,
            world.candidate,
            DocumentKind.tailored_resume,
            "Tailored resume body.",
            source_document_id=world.resume.id,
        )
        tailored_match = add_match(
            session,
            job=world.job,
            candidate=world.candidate,
            resume=tailored,
            analysis=world.analysis,
        )
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)

        with pytest.raises(InvalidMatchError, match="was computed against"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=world.resume.id,
                match_id=tailored_match.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_match_with_an_unusable_verdict_is_rejected(self, session, world) -> None:
        broken = Match(
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            resume_document_id=world.resume.id,
            job_analysis_id=world.analysis.id,
            score=72.5,
            verdict={"verdict": "pretty good"},
            model="gemini-3.8-flash",
            prompt_version="job-matching-custom",
        )
        session.add(broken)
        session.flush()
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)

        with pytest.raises(InvalidMatchError, match="unusable verdict"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=world.resume.id,
                match_id=broken.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_match_referencing_a_non_succeeded_analysis_is_rejected(
        self, session, world
    ) -> None:
        failed = add_analysis(session, world.job.id, status=RunStatus.failed)
        failed_match = add_match(
            session,
            job=world.job,
            candidate=world.candidate,
            resume=world.resume,
            analysis=failed,
            prompt_version="job-matching-custom",
        )
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)

        with pytest.raises(JobAnalysisNotFoundError, match="not a succeeded analysis"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=world.resume.id,
                match_id=failed_match.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_match_referencing_an_analysis_without_payload_is_rejected(
        self, session, world
    ) -> None:
        empty = add_analysis(
            session, world.job.id, payload=None, prompt_version="job-analysis-custom"
        )
        empty_match = add_match(
            session,
            job=world.job,
            candidate=world.candidate,
            resume=world.resume,
            analysis=empty,
            prompt_version="job-matching-custom",
        )
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)

        with pytest.raises(JobAnalysisNotFoundError, match="not a succeeded analysis"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=world.resume.id,
                match_id=empty_match.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0

    def test_match_referencing_another_jobs_analysis_is_rejected(
        self, session, world
    ) -> None:
        other_company = make_company(session, name="Elsewhere Inc.")
        other_job = make_job(session, company=other_company, title="Platform Engineer")
        other_analysis = add_analysis(session, other_job.id)
        crosswired = add_match(
            session,
            job=world.job,
            candidate=world.candidate,
            resume=world.resume,
            analysis=other_analysis,
            prompt_version="job-matching-custom",
        )
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)

        with pytest.raises(InvalidMatchError, match="belongs to job"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=world.resume.id,
                match_id=crosswired.id,
            )

        assert fake.calls == []
        assert letter_count(session) == 0


class TestGenerationWithDatabase:
    def test_generate_persists_a_cover_letter_document(self, session, world) -> None:
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)

        envelope = agent.generate(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            master_resume_document_id=world.resume.id,
            match_id=world.match.id,
        )

        assert envelope == {
            "job_id": str(world.job.id),
            "candidate_id": str(world.candidate.id),
            "master_resume_document_id": str(world.resume.id),
            "match_id": str(world.match.id),
            "document_id": envelope["document_id"],
            "prompt_version": PROMPT_VERSION,
            "model": "gemini-3.8-flash",
            "cover_letter": LETTER.model_dump(mode="json"),
        }
        assert letter_count(session) == 1

        row = first_letter(session)
        assert str(row.id) == envelope["document_id"]
        assert row.kind == DocumentKind.cover_letter
        assert row.candidate_id == world.candidate.id
        assert row.job_id == world.job.id
        assert row.source_document_id == world.resume.id
        assert row.application_id is None
        assert row.agent_run_id is None
        assert row.content == LETTER.content
        assert row.model == "gemini-3.8-flash"
        assert row.prompt_version == PROMPT_VERSION

    def test_generate_records_the_agent_run_when_supplied(
        self, session, world
    ) -> None:
        context = run_context(session)
        agent = CoverLetterAgent(FakeGeminiClient(LETTER))

        agent.generate(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            master_resume_document_id=world.resume.id,
            match_id=world.match.id,
            agent_run_id=context.run.id,
        )

        row = first_letter(session)
        assert row.agent_run_id == context.run.id

    def test_prompt_hands_gemini_the_full_evidence(self, session, world) -> None:
        fake = FakeGeminiClient(LETTER)
        agent = CoverLetterAgent(fake)

        agent.generate(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            master_resume_document_id=world.resume.id,
            match_id=world.match.id,
        )

        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["schema"] is CoverLetterResult
        assert call["system_instruction"] == SYSTEM_INSTRUCTION
        prompt = call["prompt"]
        assert "Title: Senior AI Engineer" in prompt
        assert "Company: Acme Inc." in prompt
        assert '"summary": "Backend role building agentic systems."' in prompt
        assert "Name: Ada Lovelace" in prompt
        assert "Master resume:" in prompt
        assert RESUME_TEXT in prompt
        assert "Match assessment:" in prompt
        assert "Fit score: 72.5" in prompt
        assert "Rationale: Strong backend overlap; two preferred tools absent." in prompt
        assert DESCRIPTION_MARKER not in prompt
        assert world.job.job_url not in prompt
        assert world.job.canonical_url not in prompt
        assert "searxng" not in prompt
        assert "experience_match" not in prompt

    def test_gemini_error_propagates_and_persists_nothing(
        self, session, world
    ) -> None:
        fake = FakeGeminiClient(GeminiError("quota exceeded"))
        agent = CoverLetterAgent(fake)

        with pytest.raises(GeminiError, match="quota exceeded"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=world.resume.id,
                match_id=world.match.id,
            )

        assert letter_count(session) == 0

    def test_malformed_structured_output_fails_without_persistence(
        self, session, world
    ) -> None:
        api = RecordingGeminiApi(parsed=None, text='{"confidence": 0.5}')
        agent = CoverLetterAgent(GeminiClient("gemini-3.8-flash", api=api))

        with pytest.raises(GeminiError, match="does not match the expected schema"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=world.resume.id,
                match_id=world.match.id,
            )

        assert len(api.calls) == 1
        assert "JOB EVIDENCE" in api.calls[0]["contents"]
        assert letter_count(session) == 0

    def test_out_of_range_confidence_fails_without_persistence(
        self, session, world
    ) -> None:
        payload = {**base_payload(), "confidence": 1.5}
        api = RecordingGeminiApi(parsed=None, text=json.dumps(payload))
        agent = CoverLetterAgent(GeminiClient("gemini-3.8-flash", api=api))

        with pytest.raises(GeminiError, match="does not match the expected schema"):
            agent.generate(
                session,
                job_id=world.job.id,
                candidate_id=world.candidate.id,
                master_resume_document_id=world.resume.id,
                match_id=world.match.id,
            )

        assert letter_count(session) == 0

    def test_regeneration_appends_and_history_survives(self, session, world) -> None:
        v1 = CoverLetterAgent(FakeGeminiClient(LETTER))
        first_envelope = v1.generate(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            master_resume_document_id=world.resume.id,
            match_id=world.match.id,
        )

        v2 = CoverLetterAgent(
            FakeGeminiClient(LETTER_V2, model="gemini-other"),
            prompt_version="cover-letter-v2",
        )
        second_envelope = v2.generate(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            master_resume_document_id=world.resume.id,
            match_id=world.match.id,
        )

        assert first_envelope["document_id"] != second_envelope["document_id"]
        assert first_envelope["prompt_version"] == PROMPT_VERSION
        assert second_envelope["prompt_version"] == "cover-letter-v2"
        assert letter_count(session) == 2

        rows = session.scalars(select(Document)).all()
        by_id = {str(row.id): row for row in rows}
        first_row = by_id[first_envelope["document_id"]]
        second_row = by_id[second_envelope["document_id"]]
        assert first_row.content == LETTER.content
        assert first_row.prompt_version == PROMPT_VERSION
        assert second_row.content == LETTER_V2.content
        assert second_row.prompt_version == "cover-letter-v2"
        assert first_row.source_document_id == world.resume.id
        assert second_row.source_document_id == world.resume.id

    def test_master_resume_is_never_modified(self, session, world) -> None:
        before_content = world.resume.content
        before_id = world.resume.id

        CoverLetterAgent(FakeGeminiClient(LETTER)).generate(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            master_resume_document_id=world.resume.id,
            match_id=world.match.id,
        )

        resumes = session.scalars(
            select(Document).where(Document.kind == DocumentKind.master_resume)
        ).all()
        assert len(resumes) == 1
        assert resumes[0].id == before_id
        assert resumes[0].content == before_content == RESUME_TEXT

    def test_generation_creates_no_application_and_no_agent_run(
        self, session, world
    ) -> None:
        agent_runs_before = session.scalar(select(func.count()).select_from(AgentRun))

        CoverLetterAgent(FakeGeminiClient(LETTER)).generate(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            master_resume_document_id=world.resume.id,
            match_id=world.match.id,
        )

        assert application_count(session) == 0
        assert (
            session.scalar(select(func.count()).select_from(AgentRun))
            == agent_runs_before
        )

    def test_persist_cover_letter_returns_the_stable_envelope(
        self, session, world
    ) -> None:
        envelope = persist_cover_letter(
            session,
            job_id=world.job.id,
            candidate_id=world.candidate.id,
            master_resume_document_id=world.resume.id,
            match_id=world.match.id,
            result=LETTER,
            model="model-a",
            prompt_version="cover-letter-v1",
        )

        assert set(envelope) == {
            "job_id",
            "candidate_id",
            "master_resume_document_id",
            "match_id",
            "document_id",
            "prompt_version",
            "model",
            "cover_letter",
        }
        assert envelope["model"] == "model-a"
        assert letter_count(session) == 1


class TestHandleContract:
    def test_handle_rejects_invalid_payloads(self, session) -> None:
        agent = CoverLetterAgent(FakeGeminiClient(LETTER))
        context = run_context(session)

        with pytest.raises(CoverLetterInputError, match="must be a mapping"):
            agent.handle(None, context)
        with pytest.raises(CoverLetterInputError):
            agent.handle({"job_id": "not-a-uuid"}, context)

    def test_handle_returns_the_envelope_and_links_the_run(
        self, session, world
    ) -> None:
        agent = CoverLetterAgent(FakeGeminiClient(LETTER))
        context = run_context(session)

        envelope = agent.handle(
            {
                "job_id": str(world.job.id),
                "candidate_id": str(world.candidate.id),
                "master_resume_document_id": str(world.resume.id),
                "match_id": str(world.match.id),
            },
            context,
        )

        assert envelope["job_id"] == str(world.job.id)
        assert envelope["match_id"] == str(world.match.id)
        assert envelope["prompt_version"] == PROMPT_VERSION
        assert envelope["cover_letter"] == LETTER.model_dump(mode="json")

        row = first_letter(session)
        assert str(row.id) == envelope["document_id"]
        assert row.agent_run_id == context.run.id
