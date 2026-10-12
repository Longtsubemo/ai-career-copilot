"""Public request and response schemas of the HTTP API.

The wire contract is deliberately separate from the persistence layer:
SQLAlchemy models are never serialized directly, and no API key, traceback
or internal bookkeeping field is ever part of a response. Response models
read attributes off ORM objects through ``from_attributes``; request models
reject unknown fields so a typo becomes a 422 instead of a silent no-op.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import (
    ApplicationStatus,
    AvailabilityStatus,
    CrawlStatus,
    DocumentKind,
    RunStatus,
)
from app.services.job_verification import VerificationOutcome

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(value: str) -> str:
    cleaned = value.strip()
    if not _EMAIL_PATTERN.match(cleaned):
        raise ValueError("enter a valid email address")
    return cleaned


class CandidateCreateRequest(BaseModel):
    """Body for creating the single candidate profile."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    headline: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    years_of_experience: float | None = Field(default=None, ge=0, le=100)
    seniority: str | None = Field(default=None, max_length=100)
    skills: list[str] | None = None
    summary: str | None = None

    @field_validator("name")
    @classmethod
    def _name_is_not_blank(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("name must not be empty")
        return cleaned


class CandidateUpdateRequest(BaseModel):
    """Body for partially updating the authenticated candidate profile.

    Only user-editable profile fields are declared, so identity and
    ownership columns (``id``, ``user_id``, timestamps) can never be
    written through this schema. An empty body is rejected, and ``name``
    cannot be nulled because the column is required.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    headline: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    years_of_experience: float | None = Field(default=None, ge=0, le=100)
    seniority: str | None = Field(default=None, max_length=100)
    skills: list[str] | None = None
    summary: str | None = None

    @field_validator("name")
    @classmethod
    def _name_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("name must not be empty")
        return cleaned

    @model_validator(mode="after")
    def _at_least_one_field_and_no_null_name(self) -> CandidateUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("at least one profile field must be provided")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name must not be null; omit the field instead")
        return self


class CandidateOut(BaseModel):
    """The candidate profile as exposed by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    headline: str | None
    location: str | None
    years_of_experience: float | None
    seniority: str | None
    skills: list[str] | None
    summary: str | None
    created_at: datetime
    updated_at: datetime


class ResumeOut(BaseModel):
    """One immutable master resume document."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    candidate_id: UUID
    kind: DocumentKind
    content: str
    source_document_id: UUID | None
    model: str | None
    prompt_version: str | None
    created_at: datetime


class ResumeUploadOut(BaseModel):
    """Result of one accepted master resume upload."""

    model_config = ConfigDict(from_attributes=True)

    candidate_id: UUID
    document_id: UUID
    kind: DocumentKind
    filename: str | None
    created_at: datetime


class ResumeHistoryOut(BaseModel):
    """Version history of the master resume, newest first."""

    items: list[ResumeOut]
    count: int = Field(ge=0)


class JobOut(BaseModel):
    """A discovered job posting.

    ``company_name`` is denormalized off the ``Company`` relationship by
    the route so a client never has to resolve ``company_id`` itself.

    ``availability`` is the *effective* read-time value: the stored verdict
    (``unknown``/``available``/``closed``) with age-based ``stale`` applied,
    so a client can never mistake an old, unverified posting for a confirmed
    open one. ``last_verified_at`` is when discovery last confirmed the page
    (``crawled_at``) and ``closed_at`` stamps affirmative closure evidence.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID | None
    company_name: str | None = None
    title: str
    description: str | None
    location: str | None
    employment_type: str | None
    experience_level: str | None
    job_url: str
    canonical_url: str
    source: str
    date_posted: date | None
    is_active: bool
    crawl_status: CrawlStatus
    availability: AvailabilityStatus = AvailabilityStatus.unknown
    last_verified_at: datetime | None = None
    closed_at: datetime | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime


class JobListOut(BaseModel):
    """A page of jobs plus the total number of matches."""

    items: list[JobOut]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class JobVerificationOut(BaseModel):
    """Result of one explicit re-verification check of a saved job.

    ``job`` carries the updated row so the caller never needs a second
    request, ``outcome`` says what the check established and ``message`` is a
    client-safe explanation. A ``check_failed`` outcome explicitly preserves
    the previous availability and verification timestamp.
    """

    job: JobOut
    outcome: VerificationOutcome
    message: str
    checked_at: datetime


class AgentRunOut(BaseModel):
    """Public view of one agent run: what happened, nothing internal."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_name: str
    status: RunStatus
    attempt_count: int
    max_attempts: int
    job_id: UUID | None
    candidate_id: UUID | None
    output: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _reject_nil_uuid(value: UUID | None) -> UUID | None:
    if value is not None and value.int == 0:
        raise ValueError("must not be the nil UUID")
    return value


class MatchHttpRequest(BaseModel):
    """Optional inputs of ``POST /api/jobs/{job_id}/match``.

    Everything defaults to the single candidate's current master resume
    when the field is omitted.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID | None = None
    resume_document_id: UUID | None = None

    @field_validator("candidate_id", "resume_document_id")
    @classmethod
    def _reject_nil(cls, value: UUID | None) -> UUID | None:
        return _reject_nil_uuid(value)


class CoverLetterHttpRequest(BaseModel):
    """Optional inputs of ``POST /api/jobs/{job_id}/cover-letter``.

    The match defaults to the newest one already computed for this job,
    candidate and resume.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID | None = None
    resume_document_id: UUID | None = None
    match_id: UUID | None = None

    @field_validator("candidate_id", "resume_document_id", "match_id")
    @classmethod
    def _reject_nil(cls, value: UUID | None) -> UUID | None:
        return _reject_nil_uuid(value)


class ApplicationCreateRequest(BaseModel):
    """Body for creating an application record.

    ``applied_at`` is deliberately absent: the server stamps it, so a client
    can never claim a submission time. When ``status`` is ``applied`` the
    service records the current server time.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: UUID | None = None
    status: ApplicationStatus = ApplicationStatus.draft
    notes: str | None = None


class ApplicationUpdateRequest(BaseModel):
    """Body for updating an application; at least one field is required.

    Only ``status`` and ``notes`` are editable. ``applied_at`` is intentionally
    not accepted — the server sets it on the transition to ``applied`` and
    preserves it afterwards, so no client-supplied timestamp is ever trusted.
    """

    model_config = ConfigDict(extra="forbid")

    status: ApplicationStatus | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _at_least_one_field_and_no_null_status(self) -> ApplicationUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("at least one of status or notes must be provided")
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status must not be null; omit the field instead")
        return self


class ApplicationOut(BaseModel):
    """A submitted application, treated as a historical record."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_id: UUID | None
    status: ApplicationStatus
    applied_at: datetime | None
    job_title_snapshot: str | None
    company_name_snapshot: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ApplicationListOut(BaseModel):
    """A page of applications plus the total number of matches."""

    items: list[ApplicationOut]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class RegisterRequest(BaseModel):
    """Body for creating an account and its candidate profile."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def _email_is_valid(cls, value: str) -> str:
        return _validate_email(value)


class LoginRequest(BaseModel):
    """Body for exchanging credentials for a session."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _email_is_valid(cls, value: str) -> str:
        return _validate_email(value)


class UserOut(BaseModel):
    """The authenticated account as exposed by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    created_at: datetime
