"""Synthetic job-matching benchmark fixtures and deterministic checks.

The evaluator checks explicit rubric constraints only. It cannot establish
that free-text model claims are semantically grounded; those require review.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.job_matching.contract import MatchResult, MatchVerdict

SignalField = Literal[
    "experience_match", "education_match", "location_match", "seniority_match"
]
EXPECTED_SCENARIOS = frozenset(
    {
        "strong_match",
        "partial_match",
        "missing_essential_skills",
        "unrelated_role",
        "unsupported_qualification",
        "missing_evidence",
    }
)


class JobEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    company_name: str | None = None
    location: str | None = None
    employment_type: str | None = None
    experience_level: str | None = None
    analysis: dict[str, Any]


class CandidateEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    resume_kind: str
    resume_content: str
    headline: str | None = None
    location: str | None = None
    years_of_experience: float | None = None
    seniority: str | None = None
    skills: list[str] | None = None
    summary: str | None = None


class CaseExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_verdicts: list[MatchVerdict] = Field(default_factory=list)
    must_match_skills: list[str] = Field(default_factory=list)
    must_be_missing_required: list[str] = Field(default_factory=list)
    must_be_unclear: list[SignalField] = Field(default_factory=list)
    must_not_claim_positive: list[str] = Field(default_factory=list)
    must_identify_gaps: list[str] = Field(default_factory=list)
    score_above: list[str] = Field(default_factory=list)


class MatchBenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    scenario: str
    job: JobEvidence
    candidate: CandidateEvidence
    expectations: CaseExpectations


def _normalize_phrase(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def load_cases(path: Path | None = None) -> list[MatchBenchmarkCase]:
    """Load and integrity-check the checked-in synthetic case set."""
    fixture = path or Path(__file__).with_name("job_matching_cases.json")
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("benchmark fixture must be a non-empty JSON array")
    cases = [MatchBenchmarkCase.model_validate(item) for item in raw]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("benchmark case IDs must be unique")
    scenarios = [case.scenario for case in cases]
    if len(scenarios) != len(EXPECTED_SCENARIOS) or set(scenarios) != EXPECTED_SCENARIOS:
        raise ValueError(
            "benchmark fixture must contain exactly one case for each expected scenario"
        )
    known_ids = set(ids)
    for case in cases:
        if case.id in case.expectations.score_above:
            raise ValueError(f"case {case.id} cannot rank above itself")
        unknown = set(case.expectations.score_above) - known_ids
        if unknown:
            raise ValueError(
                f"case {case.id} references unknown ranking case(s): "
                f"{', '.join(sorted(unknown))}"
            )
    return cases


def build_case_prompt(case: MatchBenchmarkCase) -> str:
    """Build a case prompt with the production matching prompt builder."""
    from app.agents.job_matching.prompt import build_matching_prompt

    return build_matching_prompt(
        job_title=case.job.title,
        company_name=case.job.company_name,
        job_location=case.job.location,
        employment_type=case.job.employment_type,
        experience_level=case.job.experience_level,
        analysis=case.job.analysis,
        candidate_name=case.candidate.name,
        headline=case.candidate.headline,
        candidate_location=case.candidate.location,
        years_of_experience=case.candidate.years_of_experience,
        seniority=case.candidate.seniority,
        skills=case.candidate.skills,
        summary=case.candidate.summary,
        resume_kind=case.candidate.resume_kind,
        resume_content=case.candidate.resume_content,
    )


def evaluate_case(
    case: MatchBenchmarkCase, match: MatchResult | dict[str, Any]
) -> dict[str, Any]:
    """Check explicit field-level expectations; no semantic LLM judging."""
    result = match if isinstance(match, MatchResult) else MatchResult.model_validate(match)
    expected = case.expectations
    checks: dict[str, bool] = {}

    if expected.allowed_verdicts:
        checks["verdict_allowed"] = result.verdict in expected.allowed_verdicts

    matched = {_normalize_phrase(item) for item in result.matched_skills}
    missing = {_normalize_phrase(item) for item in result.missing_required_skills}
    for skill in expected.must_match_skills:
        checks[f"matched_skill:{skill}"] = _normalize_phrase(skill) in matched
    for skill in expected.must_be_missing_required:
        checks[f"missing_required:{skill}"] = _normalize_phrase(skill) in missing

    for field in expected.must_be_unclear:
        checks[f"signal_unclear:{field}"] = getattr(result, field).value == "unclear"

    positive_claim_text = " ".join(result.matched_skills + result.strengths)
    positive_claim_text = _normalize_phrase(positive_claim_text)
    for phrase in expected.must_not_claim_positive:
        checks[f"not_positive_claimed:{phrase}"] = (
            _normalize_phrase(phrase) not in positive_claim_text
        )

    gap_text = _normalize_phrase(" ".join(result.gaps + [result.rationale]))
    for phrase in expected.must_identify_gaps:
        checks[f"gap_identified:{phrase}"] = _normalize_phrase(phrase) in gap_text

    return {
        "case_id": case.id,
        "verdict": result.verdict.value,
        "score": result.score,
        "checks": checks,
        "passed": all(checks.values()),
    }


def evaluate_benchmark(
    cases: list[MatchBenchmarkCase],
    outputs: dict[str, MatchResult | dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate per-case expectations and supported pairwise score ordering."""
    unknown_outputs = set(outputs) - {case.id for case in cases}
    if unknown_outputs:
        raise ValueError(f"outputs contain unknown case IDs: {', '.join(sorted(unknown_outputs))}")

    case_results = [
        evaluate_case(case, outputs[case.id])
        for case in cases
        if case.id in outputs
    ]
    normalized_outputs = {
        case_id: value
        if isinstance(value, MatchResult)
        else MatchResult.model_validate(value)
        for case_id, value in outputs.items()
    }
    ranking_checks: list[dict[str, Any]] = []
    for case in cases:
        for lower_id in case.expectations.score_above:
            if case.id not in normalized_outputs or lower_id not in normalized_outputs:
                ranking_checks.append(
                    {
                        "higher_case": case.id,
                        "lower_case": lower_id,
                        "status": "not_run",
                        "passed": False,
                    }
                )
                continue
            higher_score = normalized_outputs[case.id].score
            lower_score = normalized_outputs[lower_id].score
            ranking_checks.append(
                {
                    "higher_case": case.id,
                    "lower_case": lower_id,
                    "higher_score": higher_score,
                    "lower_score": lower_score,
                    "status": "checked",
                    "passed": higher_score > lower_score,
                }
            )

    missing_case_ids = [case.id for case in cases if case.id not in normalized_outputs]
    complete = not missing_case_ids
    passed = (
        complete
        and all(row["passed"] for row in case_results)
        and all(row["passed"] for row in ranking_checks)
    )
    return {
        "complete": complete,
        "passed": passed,
        "missing_case_ids": missing_case_ids,
        "case_results": case_results,
        "ranking_checks": ranking_checks,
        "human_review_required": [
            "Review rationale, strengths, gaps, and credential statements for semantic grounding.",
            "Check whether score ordering is sensible; no absolute score bands are defined.",
        ],
    }
