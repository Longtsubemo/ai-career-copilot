"""Behaviour of the Job Analysis agent.

Contract and prompt tests are pure; persistence tests run against
PostgreSQL through the savepoint-backed ``session`` fixture. Gemini is
always a deterministic fake — the one test that exercises the real
client injects a scripted SDK seam, so no test reaches Google's API.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agents.job_analysis import (
    JOB_ANALYSIS_AGENT_NAME,
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    InsufficientJobContentError,
    JobAnalysisAgent,
    JobAnalysisInputError,
    JobAnalysisRequest,
    JobAnalysisResult,
    JobNotFoundError,
    build_analysis_prompt,
    job_analysis_idempotency_key,
    register_job_analysis_handler,
)
from app.agents.job_analysis.agent import persist_analysis
from app.agents.runtime import HandlerRegistry, RunContext
from app.clients.gemini import GeminiClient, GeminiError
from app.models.agent_run import AgentRun
from app.models.enums import RunStatus
from app.models.job_analysis import JobAnalysis
from tests.fake_clients import FakeGeminiClient, RecordingGeminiApi
from tests.factories import make_company, make_job

ANALYSIS = JobAnalysisResult(
    summary="Senior AI engineer building agentic systems.",
    confidence=0.92,
    required_skills=["python", "fastapi", "postgresql"],
    preferred_skills=["llm orchestration"],
    responsibilities=["design agent pipelines"],
    qualifications=["3+ years backend experience"],
    experience_years_min=3,
    experience_years_max=6,
    seniority="senior",
    remote_type="hybrid",
    domain_keywords=["artificial intelligence"],
    technologies=["python"],
    education_requirements=["bachelor's degree"],
    certifications=[],
    ambiguities=["on-call expectations are unclear"],
)

ANALYSIS_V2 = JobAnalysisResult(
    summary="Second-version analysis of the same role.",
    confidence=0.7,
)


def analysis_count(session) -> int:
    return session.scalar(select(func.count()).select_from(JobAnalysis))


def get_analysis(session, job_id) -> JobAnalysis:
    row = session.scalar(
        select(JobAnalysis)
        .where(JobAnalysis.job_id == job_id)
        .order_by(JobAnalysis.created_at)
    )
    assert row is not None
    return row


def run_context(session) -> RunContext:
    run = AgentRun(
        agent_name=JOB_ANALYSIS_AGENT_NAME,
        status=RunStatus.pending,
        idempotency_key="test:job-analysis:context",
    )
    return RunContext(
        session=session, run=run, agent_name=JOB_ANALYSIS_AGENT_NAME, attempt=1
    )


class TestContract:
    def test_valid_analysis_round_trips_with_defaults(self) -> None:
        result = JobAnalysisResult(summary="A role.", confidence=0.5)

        assert result.required_skills == []
        assert result.preferred_skills == []
        assert result.responsibilities == []
        assert result.qualifications == []
        assert result.experience_years_min is None
        assert result.experience_years_max is None
        assert result.seniority is None
        assert result.remote_type is None
        assert result.ambiguities == []
        assert json.dumps(result.model_dump(mode="json"))

    def test_fully_populated_analysis_preserves_every_value(self) -> None:
        dumped = ANALYSIS.model_dump(mode="json")

        assert dumped["required_skills"] == ["python", "fastapi", "postgresql"]
        assert dumped["experience_years_min"] == 3
        assert dumped["experience_years_max"] == 6
        assert dumped["confidence"] == 0.92
        assert JobAnalysisResult.model_validate(dumped) == ANALYSIS

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"summary": ""},
            {"summary": "A role.", "confidence": 1.5},
            {"summary": "A role.", "confidence": -0.1},
            {"summary": "A role.", "confidence": 0.5, "experience_years_min": -1},
            {
                "summary": "A role.",
                "confidence": 0.5,
                "experience_years_min": 5,
                "experience_years_max": 2,
            },
            {"summary": "A role.", "confidence": 0.5, "extra": True},
            {"summary": "A role.", "confidence": "high"},
        ],
        ids=[
            "missing-summary",
            "blank-summary",
            "confidence-too-high",
            "confidence-negative",
            "negative-years",
            "inverted-range",
            "extra-field",
            "non-numeric-confidence",
        ],
    )
    def test_invalid_model_output_is_rejected(self, payload: dict) -> None:
        with pytest.raises(ValidationError):
            JobAnalysisResult.model_validate(payload)

    @pytest.mark.parametrize(
        "payload",
        [
            {"job_id": str(uuid4())},
            {"job_id": uuid4()},
        ],
        ids=["string-uuid", "native-uuid"],
    )
    def test_request_accepts_a_valid_job_id(self, payload: dict) -> None:
        request = JobAnalysisRequest.from_payload(payload)

        assert isinstance(request.job_id, type(uuid4()))

    @pytest.mark.parametrize(
        "payload",
        [None, "job_id", 7, ["job_id"]],
        ids=["none", "str", "int", "list"],
    )
    def test_request_rejects_non_mappings(self, payload: object) -> None:
        with pytest.raises(JobAnalysisInputError, match="must be a mapping"):
            JobAnalysisRequest.from_payload(payload)

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"job_id": "not-a-uuid"},
            {"job_id": str(uuid4()), "extra": 1},
            {"job_id": "00000000-0000-0000-0000-000000000000"},
        ],
        ids=["missing", "bad-uuid", "extra", "nil-uuid"],
    )
    def test_request_rejects_invalid_payloads(self, payload: dict) -> None:
        with pytest.raises(JobAnalysisInputError):
            JobAnalysisRequest.from_payload(payload)

    def test_idempotency_key_is_deterministic_and_versioned(self) -> None:
        job_id = uuid4()

        key = job_analysis_idempotency_key(job_id, PROMPT_VERSION)

        assert key == f"job_analysis:{job_id}:job-analysis-v1"
        assert key == job_analysis_idempotency_key(str(job_id), PROMPT_VERSION)
        assert job_analysis_idempotency_key(job_id, "job-analysis-v2") != key


class TestPrompt:
    def test_prompt_version_is_the_agreed_v1(self) -> None:
        assert PROMPT_VERSION == "job-analysis-v1"

    def test_prompt_contains_every_supplied_field(self) -> None:
        prompt = build_analysis_prompt(
            title="AI Engineer",
            description="Build agents.",
            company="Acme Inc.",
            location="Bangalore",
            employment_type="Full-time",
            experience_level="1-3 years",
        )

        assert "Title: AI Engineer" in prompt
        assert "Company: Acme Inc." in prompt
        assert "Location: Bangalore" in prompt
        assert "Employment type: Full-time" in prompt
        assert "Experience level: 1-3 years" in prompt
        assert prompt.endswith("Description:\nBuild agents.")

    def test_prompt_omits_absent_optional_fields(self) -> None:
        prompt = build_analysis_prompt(title="AI Engineer", description="Build.")

        assert "Company:" not in prompt
        assert "Location:" not in prompt
        assert "Employment type:" not in prompt
        assert "Experience level:" not in prompt

    def test_system_instruction_demands_no_invention(self) -> None:
        assert "Never invent" in SYSTEM_INSTRUCTION
        assert "ambiguities" in SYSTEM_INSTRUCTION
        assert "JSON" in SYSTEM_INSTRUCTION
        assert "candidate" not in SYSTEM_INSTRUCTION.lower().replace(
            "do not consider any candidate", ""
        )


class TestRegistration:
    def test_registers_under_the_default_agent_name(self) -> None:
        registry = HandlerRegistry()

        agent = register_job_analysis_handler(
            registry, gemini=FakeGeminiClient(ANALYSIS)
        )

        assert isinstance(agent, JobAnalysisAgent)
        assert agent.prompt_version == PROMPT_VERSION
        assert registry.names() == frozenset({JOB_ANALYSIS_AGENT_NAME})

    def test_supports_a_custom_name_and_version_and_replaces_by_name(self) -> None:
        registry = HandlerRegistry()

        first = register_job_analysis_handler(
            registry,
            gemini=FakeGeminiClient(ANALYSIS),
            agent_name="custom_analysis",
            prompt_version="job-analysis-v9",
        )
        register_job_analysis_handler(
            registry,
            gemini=FakeGeminiClient(ANALYSIS),
            agent_name="custom_analysis",
            prompt_version="job-analysis-v9",
        )

        assert first.prompt_version == "job-analysis-v9"
        assert registry.names() == frozenset({"custom_analysis"})

    def test_agent_construction_validates_the_prompt_version(self) -> None:
        with pytest.raises(ValueError, match="prompt_version"):
            JobAnalysisAgent(FakeGeminiClient(ANALYSIS), prompt_version="")
        with pytest.raises(ValueError, match="prompt_version"):
            JobAnalysisAgent(FakeGeminiClient(ANALYSIS), prompt_version="  ")


class TestAnalysisWithDatabase:
    def test_analyze_persists_a_succeeded_analysis(self, session) -> None:
        company = make_company(session, name="Acme Inc.")
        job = make_job(
            session,
            company=company,
            title="Senior AI Engineer",
            description="Build agentic systems in Bangalore.",
            location="Bangalore",
            employment_type="Full-time",
            experience_level="1-3 years",
        )
        fake = FakeGeminiClient(ANALYSIS)
        agent = JobAnalysisAgent(fake)

        envelope = agent.analyze(session, job.id)

        assert envelope == {
            "job_id": str(job.id),
            "prompt_version": PROMPT_VERSION,
            "model": "gemini-3.8-flash",
            "analysis": ANALYSIS.model_dump(mode="json"),
        }
        assert analysis_count(session) == 1
        row = get_analysis(session, job.id)
        assert row.status == RunStatus.succeeded
        assert row.prompt_version == PROMPT_VERSION
        assert row.model == "gemini-3.8-flash"
        assert row.analysis == ANALYSIS.model_dump(mode="json")

        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["schema"] is JobAnalysisResult
        assert call["system_instruction"] == SYSTEM_INSTRUCTION
        assert "Title: Senior AI Engineer" in call["prompt"]
        assert "Build agentic systems in Bangalore." in call["prompt"]
        assert "Company: Acme Inc." in call["prompt"]
        assert "Location: Bangalore" in call["prompt"]
        assert "Employment type: Full-time" in call["prompt"]
        assert "Experience level: 1-3 years" in call["prompt"]

    def test_missing_job_raises_without_calling_gemini(self, session) -> None:
        fake = FakeGeminiClient(ANALYSIS)
        agent = JobAnalysisAgent(fake)
        missing = uuid4()

        with pytest.raises(JobNotFoundError, match=str(missing)):
            agent.analyze(session, missing)

        assert fake.calls == []
        assert analysis_count(session) == 0

    @pytest.mark.parametrize("description", [None, "", "   "])
    def test_insufficient_content_raises_without_calling_gemini(
        self, session, description: str | None
    ) -> None:
        job = make_job(session, description=description)
        fake = FakeGeminiClient(ANALYSIS)
        agent = JobAnalysisAgent(fake)

        with pytest.raises(InsufficientJobContentError):
            agent.analyze(session, job.id)

        assert fake.calls == []
        assert analysis_count(session) == 0

    def test_repeated_analysis_short_circuits_on_the_stored_result(
        self, session
    ) -> None:
        job = make_job(session)
        fake = FakeGeminiClient(ANALYSIS)
        agent = JobAnalysisAgent(fake)

        first = agent.analyze(session, job.id)
        second = agent.analyze(session, job.id)

        assert first == second
        assert len(fake.calls) == 1
        assert analysis_count(session) == 1

    def test_new_prompt_version_creates_new_history_and_preserves_the_old(
        self, session
    ) -> None:
        job = make_job(session)
        v1 = JobAnalysisAgent(FakeGeminiClient(ANALYSIS))
        first_envelope = v1.analyze(session, job.id)

        v2 = JobAnalysisAgent(
            FakeGeminiClient(ANALYSIS_V2, model="gemini-other"),
            prompt_version="job-analysis-v2",
        )
        second_envelope = v2.analyze(session, job.id)

        assert first_envelope["prompt_version"] == PROMPT_VERSION
        assert second_envelope["prompt_version"] == "job-analysis-v2"
        assert second_envelope["model"] == "gemini-other"
        assert second_envelope["analysis"] == ANALYSIS_V2.model_dump(mode="json")
        assert analysis_count(session) == 2

        rows = session.scalars(
            select(JobAnalysis).where(JobAnalysis.job_id == job.id)
        ).all()
        by_version = {row.prompt_version: row for row in rows}
        assert by_version[PROMPT_VERSION].analysis == ANALYSIS.model_dump(
            mode="json"
        )
        assert by_version[PROMPT_VERSION].model == "gemini-3.8-flash"
        assert by_version["job-analysis-v2"].analysis == ANALYSIS_V2.model_dump(
            mode="json"
        )

    def test_conflicting_persist_keeps_the_first_writer(self, session) -> None:
        job = make_job(session)
        first = JobAnalysisResult(summary="first writer", confidence=0.4)
        second = JobAnalysisResult(summary="second writer", confidence=0.8)

        envelope_one = persist_analysis(
            session,
            job_id=job.id,
            result=first,
            model="model-a",
            prompt_version=PROMPT_VERSION,
        )
        envelope_two = persist_analysis(
            session,
            job_id=job.id,
            result=second,
            model="model-b",
            prompt_version=PROMPT_VERSION,
        )

        assert envelope_one["analysis"] == first.model_dump(mode="json")
        assert envelope_two["analysis"] == first.model_dump(mode="json")
        assert envelope_two["model"] == "model-a"
        assert analysis_count(session) == 1

    def test_gemini_error_propagates_and_persists_nothing(
        self, session
    ) -> None:
        job = make_job(session)
        fake = FakeGeminiClient(GeminiError("quota exceeded"))
        agent = JobAnalysisAgent(fake)

        with pytest.raises(GeminiError, match="quota exceeded"):
            agent.analyze(session, job.id)

        assert analysis_count(session) == 0

    def test_malformed_structured_output_fails_without_persistence(
        self, session
    ) -> None:
        job = make_job(session)
        api = RecordingGeminiApi(parsed=None, text='{"unexpected": true}')
        agent = JobAnalysisAgent(GeminiClient("gemini-3.8-flash", api=api))

        with pytest.raises(GeminiError, match="does not match the expected schema"):
            agent.analyze(session, job.id)

        assert len(api.calls) == 1
        assert "Title:" in api.calls[0]["contents"]
        assert analysis_count(session) == 0


class TestHandleContract:
    def test_handle_rejects_invalid_payloads(self, session) -> None:
        agent = JobAnalysisAgent(FakeGeminiClient(ANALYSIS))
        context = run_context(session)

        with pytest.raises(JobAnalysisInputError, match="must be a mapping"):
            agent.handle(None, context)
        with pytest.raises(JobAnalysisInputError):
            agent.handle({"job_id": "not-a-uuid"}, context)

    def test_handle_returns_the_structured_envelope(self, session) -> None:
        job = make_job(session)
        agent = JobAnalysisAgent(FakeGeminiClient(ANALYSIS))
        context = run_context(session)

        envelope = agent.handle({"job_id": str(job.id)}, context)

        assert envelope["job_id"] == str(job.id)
        assert envelope["analysis"] == ANALYSIS.model_dump(mode="json")
        assert analysis_count(session) == 1
