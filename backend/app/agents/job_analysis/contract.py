"""Structured input/output contract of the Job Analysis agent.

The input is a job reference; the output is a Pydantic-validated semantic
analysis produced by Gemini. :class:`JobAnalysisResult` is the exact
schema handed to Gemini as its structured-output schema, so validation
happens at the client boundary and again wherever the stored JSON is
re-validated. LLM output is data only — none of these types participates
in runtime state transitions.
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
    model_validator,
)

JOB_ANALYSIS_AGENT_NAME = "job_analysis"


class JobAnalysisInputError(ValueError):
    """The job analysis request failed deterministic validation."""


class JobNotFoundError(LookupError):
    """The requested job id does not exist in the jobs table."""


class InsufficientJobContentError(ValueError):
    """The job exists but carries no content worth sending to Gemini."""


class JobAnalysisRequest(BaseModel):
    """A job analysis request: exactly one required ``job_id``."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID

    @field_validator("job_id")
    @classmethod
    def _reject_nil_uuid(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("job_id must not be the nil UUID")
        return value

    @classmethod
    def from_payload(cls, payload: object) -> JobAnalysisRequest:
        """Validate a raw handler payload, raising :class:`JobAnalysisInputError`."""
        if not isinstance(payload, Mapping):
            raise JobAnalysisInputError(
                f"job analysis input must be a mapping, got {type(payload).__name__}"
            )
        try:
            return cls.model_validate(dict(payload))
        except ValidationError as exc:
            raise JobAnalysisInputError(str(exc)) from exc


class JobAnalysisResult(BaseModel):
    """Semantic analysis of a single job posting.

    Deliberately excludes fields the ``jobs`` table already stores
    deterministically (title, location, employment type, company name);
    this schema only carries what Gemini infers or extracts from the
    posting text. Nullable numeric ranges and defaulted lists mean the
    absence of information is representable without inventing values.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, description="Concise factual summary of the role.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="How well-supported this analysis is by the posting."
    )

    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)

    experience_years_min: int | None = Field(default=None, ge=0)
    experience_years_max: int | None = Field(default=None, ge=0)

    seniority: str | None = None
    remote_type: str | None = None

    domain_keywords: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    education_requirements: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(
        default_factory=list,
        description="Ambiguities or gaps observed in the posting, never guessed away.",
    )

    @model_validator(mode="after")
    def _experience_range_is_ordered(self) -> JobAnalysisResult:
        if (
            self.experience_years_min is not None
            and self.experience_years_max is not None
            and self.experience_years_min > self.experience_years_max
        ):
            raise ValueError(
                "experience_years_min must not exceed experience_years_max"
            )
        return self


def job_analysis_idempotency_key(job_id: UUID | str, prompt_version: str) -> str:
    """Return the deterministic idempotency key for a job analysis run.

    ``job_analysis:{job_id}:{prompt_version}`` — same job and prompt
    version always collapse onto one run, a new prompt version always
    gets a fresh one.
    """
    return f"job_analysis:{job_id}:{prompt_version}"
