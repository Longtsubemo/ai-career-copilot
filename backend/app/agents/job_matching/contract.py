"""Structured input/output contract of the Job Matching agent.

The input identifies exactly one matching computation: a job, a candidate
and the resume document used as candidate evidence. The output schema is
the semantic fit assessment handed to Gemini; the deterministic envelope
around it carries the provenance (which JobAnalysis the match was based
on, which prompt version, which model) needed for reproducibility. LLM
output is data only — none of these types participates in runtime state
transitions.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

JOB_MATCHING_AGENT_NAME = "job_matching"


class JobMatchingInputError(ValueError):
    """The job matching request failed deterministic validation."""


class JobNotFoundError(LookupError):
    """The requested job id does not exist in the jobs table."""


class CandidateNotFoundError(LookupError):
    """The requested candidate id does not exist in the candidates table."""


class ResumeDocumentNotFoundError(LookupError):
    """The requested resume document id does not exist in the documents table."""


class JobAnalysisNotFoundError(LookupError):
    """No succeeded, usable job analysis exists for the requested job."""


class InvalidResumeDocumentError(ValueError):
    """The resume document cannot serve as evidence for this matching request."""


class InsufficientCandidateProfileError(ValueError):
    """The candidate profile carries no evidence worth matching against."""


class MatchVerdict(StrEnum):
    """Constrained vocabulary for the overall fit verdict."""

    strong_match = "strong_match"
    good_match = "good_match"
    partial_match = "partial_match"
    weak_match = "weak_match"
    poor_match = "poor_match"


class MatchSignal(StrEnum):
    """Tri-state assessment of one deterministic dimension of fit.

    ``unclear`` exists so the model can honour absent evidence instead of
    inventing a negative.
    """

    matched = "matched"
    mismatched = "mismatched"
    unclear = "unclear"


class JobMatchingRequest(BaseModel):
    """Exactly one matching computation: job + candidate + resume document."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    candidate_id: UUID
    resume_document_id: UUID

    @field_validator("job_id", "candidate_id", "resume_document_id")
    @classmethod
    def _reject_nil_uuids(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("ids must not be the nil UUID")
        return value

    @classmethod
    def from_payload(cls, payload: object) -> JobMatchingRequest:
        """Validate a raw handler payload, raising :class:`JobMatchingInputError`."""
        if not isinstance(payload, Mapping):
            raise JobMatchingInputError(
                f"job matching input must be a mapping, got {type(payload).__name__}"
            )
        try:
            return cls.model_validate(dict(payload))
        except ValidationError as exc:
            raise JobMatchingInputError(str(exc)) from exc


class MatchResult(BaseModel):
    """Structured candidate/job fit assessment produced by Gemini.

    Deliberately describes the *relationship* between the candidate and
    the job rather than restating deterministic job fields. The score is
    bounded to the canonical application scale (0-100) and rejected —
    never clamped — when the model produces anything outside it, so an
    out-of-scale answer fails validation instead of silently corrupting
    the product's score semantics.
    """

    model_config = ConfigDict(extra="forbid")

    score: float = Field(
        ge=0.0, le=100.0, description="Overall candidate/job fit on the 0-100 scale."
    )
    verdict: MatchVerdict

    matched_skills: list[str] = Field(default_factory=list)
    missing_required_skills: list[str] = Field(default_factory=list)
    missing_preferred_skills: list[str] = Field(default_factory=list)

    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

    experience_match: MatchSignal
    education_match: MatchSignal
    location_match: MatchSignal
    seniority_match: MatchSignal

    rationale: str = Field(min_length=1)
    confidence: float = Field(
        ge=0.0, le=1.0, description="How well-supported this assessment is by the evidence."
    )

    @field_validator("rationale", mode="after")
    @classmethod
    def _reject_blank_rationale(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("rationale must not be blank")
        return stripped


class MatchEnvelope(BaseModel):
    """Stable result envelope returned on fresh and reuse paths alike."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    candidate_id: UUID
    resume_document_id: UUID
    job_analysis_id: UUID
    prompt_version: str
    model: str | None = None
    match: MatchResult


def job_matching_idempotency_key(
    job_id: UUID | str,
    candidate_id: UUID | str,
    resume_document_id: UUID | str,
    prompt_version: str,
) -> str:
    """Return the deterministic idempotency key for a matching run.

    ``job_matching:{job_id}:{candidate_id}:{resume_document_id}:{prompt_version}`
    — the exact inputs that define the computation, never a random UUID.
    """
    return (
        f"job_matching:{job_id}:{candidate_id}:"
        f"{resume_document_id}:{prompt_version}"
    )
