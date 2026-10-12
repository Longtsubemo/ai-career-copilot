"""Structured input/output contract of the Cover Letter agent.

The input identifies exactly one generation: a job, a candidate, the
candidate's uploaded master resume and the persisted match the letter is
grounded in. :class:`CoverLetterResult` is the exact schema handed to
Gemini as its structured-output schema, so validation happens at the
client boundary and again wherever the stored document is re-read. The
deterministic envelope around it carries the provenance (which document
row was written, which prompt version, which model) needed for
reproducibility. LLM output is data only — none of these types
participates in runtime state transitions.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

COVER_LETTER_AGENT_NAME = "cover_letter"


class CoverLetterInputError(ValueError):
    """The cover letter request failed deterministic validation."""


class JobNotFoundError(LookupError):
    """The requested job id does not exist in the jobs table."""


class CandidateNotFoundError(LookupError):
    """The requested candidate id does not exist in the candidates table."""


class MasterResumeNotFoundError(LookupError):
    """The requested master resume id does not exist in the documents table."""


class InvalidMasterResumeError(ValueError):
    """The document cannot serve as master resume evidence for this request."""


class MatchNotFoundError(LookupError):
    """The requested match id does not exist in the matches table."""


class InvalidMatchError(ValueError):
    """The match does not belong to this job/candidate/resume or is unusable."""


class JobAnalysisNotFoundError(LookupError):
    """The match's job analysis is missing or is not a succeeded analysis."""


class CoverLetterRequest(BaseModel):
    """Exactly one generation: job + candidate + master resume + match."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    candidate_id: UUID
    master_resume_document_id: UUID
    match_id: UUID

    @field_validator(
        "job_id", "candidate_id", "master_resume_document_id", "match_id"
    )
    @classmethod
    def _reject_nil_uuids(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("ids must not be the nil UUID")
        return value

    @classmethod
    def from_payload(cls, payload: object) -> CoverLetterRequest:
        """Validate a raw handler payload, raising :class:`CoverLetterInputError`."""
        if not isinstance(payload, Mapping):
            raise CoverLetterInputError(
                f"cover letter input must be a mapping, got {type(payload).__name__}"
            )
        try:
            return cls.model_validate(dict(payload))
        except ValidationError as exc:
            raise CoverLetterInputError(str(exc)) from exc


class CoverLetterResult(BaseModel):
    """The generated cover letter plus how well it is grounded.

    Deliberately minimal: ordinary prose is not over-structured. The
    letter body is rejected when blank — whitespace is never a usable
    document — and ``confidence`` is bounded to 0-1 and rejected, never
    clamped, when the model produces anything outside it.
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(
        min_length=1, description="The full cover letter text."
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="How well the letter is grounded in the evidence."
    )

    @field_validator("content", mode="after")
    @classmethod
    def _reject_blank_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped


class CoverLetterEnvelope(BaseModel):
    """Stable result envelope returned for every generation."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    candidate_id: UUID
    master_resume_document_id: UUID
    match_id: UUID
    document_id: UUID
    prompt_version: str
    model: str | None = None
    cover_letter: CoverLetterResult


def cover_letter_idempotency_key(
    job_id: UUID | str,
    candidate_id: UUID | str,
    master_resume_document_id: UUID | str,
    match_id: UUID | str,
    prompt_version: str,
) -> str:
    """Return the deterministic idempotency key for a cover letter run.

    ``cover_letter:{job_id}:{candidate_id}:{master_resume_document_id}:
    {match_id}:{prompt_version}`` — the exact inputs that define the
    generation, never a random UUID. ``match_id`` is part of the key
    because it identifies the specific analysis/resume pairing the letter
    is grounded in.
    """
    return (
        f"cover_letter:{job_id}:{candidate_id}:"
        f"{master_resume_document_id}:{match_id}:{prompt_version}"
    )
