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
    JobAnalysisResponse,
    JobNotFoundError,
    JobRequirement,
    RequirementCategory,
    RequirementStatus,
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
        ("item", "category", "status", "evidence"),
        [
            (
                "AWS certification",
                "certification",
                "required",
                ["AWS certification is required."],
            ),
            (
                "AWS certification",
                "certification",
                "preferred",
                ["AWS certification is preferred."],
            ),
            (
                "AWS certification",
                "certification",
                "unclear",
                ["AWS certification"],
            ),
            (
                "CPA license",
                "qualification",
                "required",
                ["Candidates must hold an active CPA license."],
            ),
            (
                "Kubernetes",
                "skill",
                "preferred",
                ["Experience with Kubernetes is desirable."],
            ),
            (
                "AWS certification",
                "certification",
                "unclear",
                [
                    "AWS certification is required.",
                    "AWS certification is preferred.",
                ],
            ),
            (
                "AWS certification",
                "certification",
                "unclear",
                ["AWS certification may be obtained after joining."],
            ),
            (
                "AWS certification",
                "certification",
                "required",
                ["AWS certification and Azure certification are required."],
            ),
            (
                "Azure certification",
                "certification",
                "required",
                ["AWS certification and Azure certification are required."],
            ),
        ],
        ids=[
            "required-certification",
            "preferred-certification",
            "generic-certification-unclear",
            "required-qualification",
            "preferred-skill",
            "contradictory-wording",
            "conditional-certification",
            "compound-aws",
            "compound-azure",
        ],
    )
    def test_requirement_item_represents_status_and_evidence(
        self,
        item: str,
        category: str,
        status: str,
        evidence: list[str],
    ) -> None:
        result = JobRequirement.model_validate(
            {
                "item": item,
                "category": category,
                "status": status,
                "evidence": evidence,
            }
        )

        assert result.item == item
        assert result.category == RequirementCategory(category)
        assert result.status == RequirementStatus(status)
        assert result.evidence == evidence
        assert JobRequirement.model_validate(result.model_dump(mode="json")) == result

    @pytest.mark.parametrize(
        "payload",
        [
            {
                "item": "AWS certification",
                "category": "certification",
                "status": "required",
            },
            {
                "item": "AWS certification",
                "category": "certification",
                "status": "required",
                "evidence": [],
            },
            {
                "item": "AWS certification",
                "category": "certification",
                "status": "required",
                "evidence": ["   "],
            },
            {
                "item": "AWS certification",
                "category": "certification",
                "status": "mandatory",
                "evidence": ["AWS certification is required."],
            },
            {
                "item": "AWS certification",
                "category": "license",
                "status": "required",
                "evidence": ["AWS certification is required."],
            },
            {
                "item": "AWS certification",
                "category": "certification",
                "status": "required",
                "evidence": ["AWS certification is required."],
                "extra": "not allowed",
            },
        ],
        ids=[
            "missing-evidence",
            "empty-evidence",
            "blank-evidence",
            "bad-status",
            "bad-category",
            "extra-field",
        ],
    )
    def test_invalid_requirement_item_is_rejected(self, payload: dict) -> None:
        with pytest.raises(ValidationError):
            JobRequirement.model_validate(payload)

    def test_requirement_item_rejects_a_blank_name(self) -> None:
        with pytest.raises(ValidationError, match="item must not be blank"):
            JobRequirement.model_validate(
                {
                    "item": "  ",
                    "category": "certification",
                    "status": "required",
                    "evidence": ["AWS certification is required."],
                }
            )

    def test_evidence_for_an_unrelated_requirement_is_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="each evidence excerpt must name its requirement item"
        ):
            JobRequirement(
                item="AWS certification",
                category=RequirementCategory.certification,
                status=RequirementStatus.required,
                evidence=["CPA license is required."],
            )

    def test_required_or_preferred_evidence_needs_a_status_cue(self) -> None:
        for status in (RequirementStatus.required, RequirementStatus.preferred):
            with pytest.raises(ValidationError, match="needs an associated explicit"):
                JobRequirement(
                    item="AWS certification",
                    category=RequirementCategory.certification,
                    status=status,
                    evidence=["AWS certification"],
                )

    def test_valid_required_and_preferred_evidence_matches_source(self) -> None:
        for status, excerpt in (
            (RequirementStatus.required, "AWS certification is required."),
            (RequirementStatus.preferred, "AWS certification is a plus."),
        ):
            result = JobAnalysisResult(
                summary="A role.",
                confidence=0.8,
                requirement_items=[
                    JobRequirement(
                        item="AWS certification",
                        category=RequirementCategory.certification,
                        status=status,
                        evidence=[excerpt],
                    )
                ],
            )
            result.validate_requirement_evidence(f"Other text. {excerpt}")

    def test_evidence_matching_normalizes_unicode_but_keeps_punctuation(self) -> None:
        source = "Café certification is required!"
        evidence = "Cafe\u0301 certification is required!"
        requirement = JobRequirement(
            item="Café certification",
            category=RequirementCategory.certification,
            status=RequirementStatus.required,
            evidence=[evidence],
        )

        requirement.validate_evidence_against(source)
        with pytest.raises(ValueError, match="not present in the supplied job description"):
            requirement.validate_evidence_against("Café certification is required.")

    def test_short_generic_evidence_is_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="each evidence excerpt must name its requirement item"
        ):
            JobRequirement(
                item="AWS certification",
                category=RequirementCategory.certification,
                status=RequirementStatus.unclear,
                evidence=["AWS"],
            )

    def test_duplicate_evidence_excerpts_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="evidence excerpts must not be duplicated"):
            JobRequirement(
                item="AWS certification",
                category=RequirementCategory.certification,
                status=RequirementStatus.required,
                evidence=[
                    "AWS certification is required.",
                    " aws certification IS REQUIRED. ",
                ],
            )

    def test_negative_status_cues_do_not_satisfy_required_or_preferred(self) -> None:
        for status, evidence in (
            (RequirementStatus.required, "AWS certification is not required."),
            (RequirementStatus.required, "No AWS certification is required."),
            (RequirementStatus.preferred, "AWS certification is not preferred."),
        ):
            with pytest.raises(ValidationError, match="needs an associated explicit"):
                JobRequirement(
                    item="AWS certification",
                    category=RequirementCategory.certification,
                    status=status,
                    evidence=[evidence],
                )

    @pytest.mark.parametrize(
        ("evidence", "status", "accepted"),
        [
            (
                "AWS certification is required.",
                RequirementStatus.required,
                True,
            ),
            (
                "No AWS certification is required.",
                RequirementStatus.required,
                False,
            ),
            (
                "AWS certification is not required but preferred.",
                RequirementStatus.preferred,
                True,
            ),
            (
                "AWS certification is not mandatory.",
                RequirementStatus.required,
                False,
            ),
            (
                "AWS certification is not required and is preferred.",
                RequirementStatus.preferred,
                True,
            ),
            (
                "AWS certification is required, not preferred.",
                RequirementStatus.preferred,
                False,
            ),
        ],
        ids=[
            "explicit-required",
            "no-certification-required",
            "not-required-but-preferred",
            "not-mandatory",
            "not-required-and-preferred",
            "required-not-preferred",
        ],
    )
    def test_negation_is_scoped_to_the_relevant_status_cue(
        self, evidence, status, accepted
    ) -> None:
        payload = {
            "item": "AWS certification",
            "category": RequirementCategory.certification,
            "status": status,
            "evidence": [evidence],
        }
        if accepted:
            JobRequirement.model_validate(payload)
        else:
            with pytest.raises(ValidationError):
                JobRequirement.model_validate(payload)

    def test_negated_mandatory_wording_can_remain_unclear(self) -> None:
        requirement = JobRequirement(
            item="AWS certification",
            category=RequirementCategory.certification,
            status=RequirementStatus.unclear,
            evidence=["AWS certification is not mandatory."],
        )

        assert requirement.status is RequirementStatus.unclear

    def test_contradictory_negated_and_positive_required_cues_must_be_unclear(
        self,
    ) -> None:
        evidence = "AWS certification is not required and mandatory."

        with pytest.raises(ValidationError):
            JobRequirement(
                item="AWS certification",
                category=RequirementCategory.certification,
                status=RequirementStatus.required,
                evidence=[evidence],
            )

        unclear = JobRequirement(
            item="AWS certification",
            category=RequirementCategory.certification,
            status=RequirementStatus.unclear,
            evidence=[evidence],
        )
        assert unclear.status is RequirementStatus.unclear

    def test_another_items_status_cue_does_not_classify_this_item(self) -> None:
        evidence = (
            "AWS certification is not required and CPA license is preferred."
        )

        with pytest.raises(ValidationError):
            JobRequirement(
                item="AWS certification",
                category=RequirementCategory.certification,
                status=RequirementStatus.preferred,
                evidence=[evidence],
            )

        cpa = JobRequirement(
            item="CPA license",
            category=RequirementCategory.qualification,
            status=RequirementStatus.preferred,
            evidence=[evidence],
        )
        assert cpa.status is RequirementStatus.preferred

    def test_independent_requirements_in_one_sentence_keep_their_statuses(self) -> None:
        evidence = "AWS certification is required and CPA license is preferred."

        aws = JobRequirement(
            item="AWS certification",
            category=RequirementCategory.certification,
            status=RequirementStatus.required,
            evidence=[evidence],
        )
        cpa = JobRequirement(
            item="CPA license",
            category=RequirementCategory.qualification,
            status=RequirementStatus.preferred,
            evidence=[evidence],
        )

        assert aws.status is RequirementStatus.required
        assert cpa.status is RequirementStatus.preferred

    def test_one_item_with_conflicting_evidence_must_be_unclear(self) -> None:
        with pytest.raises(
            ValidationError, match="conflicting required/preferred evidence"
        ):
            JobRequirement(
                item="AWS certification",
                category=RequirementCategory.certification,
                status=RequirementStatus.required,
                evidence=[
                    "AWS certification is required.",
                    "AWS certification is preferred.",
                ],
            )

    def test_requirement_evidence_must_be_found_in_source_text(self) -> None:
        requirement = JobRequirement(
            item="AWS certification",
            category=RequirementCategory.certification,
            status=RequirementStatus.unclear,
            evidence=["AWS certification is required."],
        )
        result = JobAnalysisResult(
            summary="A role.", confidence=0.8, requirement_items=[requirement]
        )

        result.validate_requirement_evidence(
            "Qualifications:\nAWS certification is REQUIRED."
        )
        with pytest.raises(
            ValueError, match="not present in the supplied job description"
        ):
            result.validate_requirement_evidence("Qualifications: CPA license required.")

    def test_contradictory_excerpts_are_valid_only_when_both_are_in_source(self) -> None:
        result = JobAnalysisResponse(
            summary="A role.",
            confidence=0.6,
            requirement_items=[
                JobRequirement(
                    item="AWS certification",
                    category=RequirementCategory.certification,
                    status=RequirementStatus.unclear,
                    evidence=[
                        "AWS certification is required.",
                        "AWS certification is preferred.",
                    ],
                )
            ],
        )

        result.validate_requirement_evidence(
            "AWS certification is required. Elsewhere, AWS certification is preferred."
        )
        with pytest.raises(
            ValueError, match="not present in the supplied job description"
        ):
            result.validate_requirement_evidence("AWS certification is required.")

    @pytest.mark.parametrize(
        "second_status",
        [RequirementStatus.required, RequirementStatus.preferred],
        ids=["same-status", "conflicting-status"],
    )
    def test_duplicate_requirement_items_are_rejected(self, second_status) -> None:
        first = JobRequirement(
            item="AWS certification",
            category=RequirementCategory.certification,
            status=RequirementStatus.required,
            evidence=["AWS certification is required."],
        )
        second_evidence = (
            "AWS certification is required."
            if second_status is RequirementStatus.required
            else "AWS certification is preferred."
        )
        second = JobRequirement(
            item=" aws   CERTIFICATION ",
            category=RequirementCategory.certification,
            status=second_status,
            evidence=[second_evidence],
        )

        with pytest.raises(
            ValidationError, match="duplicate requirement item"
        ):
            JobAnalysisResponse(
                summary="A role.",
                confidence=0.8,
                requirement_items=[first, second],
            )

    def test_unclear_single_item_preserves_both_conflicting_excerpts(self) -> None:
        result = JobAnalysisResponse(
            summary="A role.",
            confidence=0.6,
            requirement_items=[
                JobRequirement(
                    item="AWS certification",
                    category=RequirementCategory.certification,
                    status=RequirementStatus.unclear,
                    evidence=[
                        "AWS certification is required.",
                        "AWS certification is preferred.",
                    ],
                )
            ],
        )

        result.validate_requirement_evidence(
            "AWS certification is required. AWS certification is preferred."
        )
        assert result.requirement_items[0].status is RequirementStatus.unclear
        assert len(result.requirement_items[0].evidence) == 2

    @pytest.mark.parametrize(
        ("status", "legacy_required", "legacy_preferred", "error"),
        [
            (RequirementStatus.required, ["Kubernetes"], [], None),
            (RequirementStatus.preferred, [], ["Kubernetes"], None),
            (RequirementStatus.unclear, [], [], None),
            (
                RequirementStatus.required,
                [],
                ["Kubernetes"],
                "structured required skills",
            ),
            (
                RequirementStatus.preferred,
                ["Kubernetes"],
                [],
                "structured preferred skills",
            ),
            (
                RequirementStatus.unclear,
                ["Kubernetes"],
                [],
                "structured unclear skills",
            ),
            (
                RequirementStatus.unclear,
                [],
                ["Kubernetes"],
                "structured unclear skills",
            ),
        ],
        ids=[
            "required-skill-in-required-array",
            "preferred-skill-in-preferred-array",
            "unclear-skill-in-neither-array",
            "required-skill-not-in-required-array",
            "preferred-skill-not-in-preferred-array",
            "unclear-skill-not-required",
            "unclear-skill-not-preferred",
        ],
    )
    def test_structured_skill_status_matches_legacy_arrays(
        self, status, legacy_required, legacy_preferred, error
    ) -> None:
        cue = {
            RequirementStatus.required: "Kubernetes is required.",
            RequirementStatus.preferred: "Kubernetes is preferred.",
            RequirementStatus.unclear: "Kubernetes experience.",
        }[status]
        payload = {
            "summary": "A role.",
            "confidence": 0.8,
            "required_skills": legacy_required,
            "preferred_skills": legacy_preferred,
            "requirement_items": [
                {
                    "item": "Kubernetes",
                    "category": "skill",
                    "status": status.value,
                    "evidence": [cue],
                }
            ],
        }
        if error is None:
            result = JobAnalysisResponse.model_validate(payload)
            assert result.requirement_items[0].status is status
        else:
            with pytest.raises(ValidationError, match=error):
                JobAnalysisResponse.model_validate(payload)

    def test_a_skill_cannot_appear_in_both_legacy_arrays(self) -> None:
        with pytest.raises(ValidationError, match="must not appear in both"):
            JobAnalysisResponse(
                summary="A role.",
                confidence=0.8,
                required_skills=["Kubernetes"],
                preferred_skills=[" kubernetes "],
                requirement_items=[],
            )

    def test_historical_analysis_without_requirement_items_remains_valid(self) -> None:
        historical = ANALYSIS.model_dump(mode="json", exclude={"requirement_items"})

        loaded = JobAnalysisResult.model_validate(historical)

        assert loaded.requirement_items == []
        assert loaded.qualifications == ANALYSIS.qualifications
        assert loaded.certifications == ANALYSIS.certifications

    def test_new_model_response_requires_requirement_items(self) -> None:
        legacy_payload = ANALYSIS.model_dump(
            mode="json", exclude={"requirement_items"}
        )

        with pytest.raises(ValidationError):
            JobAnalysisResponse.model_validate(legacy_payload)

        schema = JobAnalysisResponse.model_json_schema()
        assert "requirement_items" in schema["required"]

    def test_requirement_items_round_trip_in_analysis_serialization(self) -> None:
        result = JobAnalysisResult(
            summary="A role.",
            confidence=0.8,
            requirement_items=[
                JobRequirement(
                    item="AWS certification",
                    category=RequirementCategory.certification,
                    status=RequirementStatus.required,
                    evidence=["AWS certification is required."],
                )
            ],
        )

        serialized = result.model_dump(mode="json")
        restored = JobAnalysisResult.model_validate(serialized)

        assert serialized["requirement_items"][0] == {
            "item": "AWS certification",
            "category": "certification",
            "status": "required",
            "evidence": ["AWS certification is required."],
        }
        assert restored == result

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

        assert key == f"job_analysis:{job_id}:job-analysis-v2"
        assert key == job_analysis_idempotency_key(str(job_id), PROMPT_VERSION)
        assert job_analysis_idempotency_key(job_id, "job-analysis-v3") != key


class TestPrompt:
    def test_prompt_version_is_the_agreed_v2(self) -> None:
        assert PROMPT_VERSION == "job-analysis-v2"

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

    def test_system_instruction_classifies_and_preserves_requirement_evidence(
        self,
    ) -> None:
        instruction = " ".join(SYSTEM_INSTRUCTION.split()).casefold()

        assert '"requirement_items"' in instruction
        assert '"required", "preferred", or "unclear"' in instruction
        assert (
            'generic list entry or mention without a clear status is "unclear"'
            in instruction
        )
        assert "preserve excerpts for both statements" in instruction
        assert "may be obtained after joining" in instruction
        assert "verbatim" in instruction
        assert "split compound requirements into separate items" in instruction
        assert "legacy compatibility lists" in instruction
        assert "explicitly makes it mandatory" in instruction
        assert "explicitly presents it as preferred" in instruction
        assert (
            "do not infer mandatory status from a qualifications or certifications heading"
            in instruction
        )
        assert "every evidence excerpt must name or unambiguously identify its item" in instruction
        assert "in the same excerpt" in instruction
        assert "do not by themselves prove semantic entailment" in instruction


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
        assert call["schema"] is JobAnalysisResponse
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
            prompt_version="job-analysis-v3",
        )
        second_envelope = v2.analyze(session, job.id)

        assert first_envelope["prompt_version"] == PROMPT_VERSION
        assert second_envelope["prompt_version"] == "job-analysis-v3"
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
        assert by_version["job-analysis-v3"].analysis == ANALYSIS_V2.model_dump(
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

        with pytest.raises(GeminiError, match="Gemini request failed"):
            agent.analyze(session, job.id)

        assert analysis_count(session) == 0

    def test_malformed_structured_output_fails_without_persistence(
        self, session
    ) -> None:
        job = make_job(session)
        api = RecordingGeminiApi(parsed=None, text='{"unexpected": true}')
        agent = JobAnalysisAgent(GeminiClient("gemini-3.8-flash", api=api))

        with pytest.raises(GeminiError, match="Gemini returned unusable structured output"):
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
