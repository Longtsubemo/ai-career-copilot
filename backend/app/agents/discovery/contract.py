"""Structured input/output contract of the Job Discovery agent.

Everything here is deterministic: field validation, whitespace/case
normalization and the idempotency key are pure functions. No LLM is
involved at any point.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

DISCOVERY_AGENT_NAME = "job_discovery"

MAX_DISCOVERY_ISSUES = 25


class DiscoveryInputError(ValueError):
    """The discovery request failed deterministic validation."""


class DiscoveryIssueCategory(StrEnum):
    """Why a candidate was skipped or degraded during discovery.

    Categories are factual labels only: a page is never described as
    bot-protected without evidence. ``network_error`` covers transport and
    non-timeout HTTP failures, ``http_error`` an HTTP status response,
    ``timeout`` a request deadline and ``access_denied`` an explicit
    401/403/429 response.
    """

    search_failure = "search_failure"
    network_error = "network_error"
    timeout = "timeout"
    http_error = "http_error"
    access_denied = "access_denied"
    empty_extraction = "empty_extraction"
    invalid_url = "invalid_url"
    listing_page = "listing_page"
    validation_rejected = "validation_rejected"
    duplicate = "duplicate"
    scrape_failure = "scrape_failure"


class DiscoveryIssue(BaseModel):
    """One bounded, sanitized diagnostic record from a discovery run."""

    model_config = ConfigDict(extra="forbid")

    url: str
    stage: str
    category: DiscoveryIssueCategory
    status: int | None = None
    message: str = ""


class DiscoveryRequest(BaseModel):
    """A discovery request: three required, non-empty search constraints."""

    model_config = ConfigDict(extra="forbid")

    role: str
    location: str
    experience: str

    @field_validator("role", "location", "experience")
    @classmethod
    def _trim_and_reject_blank(cls, value: str, info: ValidationInfo) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError(f"{info.field_name} must not be empty")
        return cleaned

    @classmethod
    def from_payload(cls, payload: object) -> DiscoveryRequest:
        """Validate a raw handler payload, raising :class:`DiscoveryInputError`."""
        if not isinstance(payload, Mapping):
            raise DiscoveryInputError(
                f"discovery input must be a mapping, got {type(payload).__name__}"
            )
        try:
            return cls.model_validate(dict(payload))
        except ValidationError as exc:
            raise DiscoveryInputError(str(exc)) from exc


class DiscoveryResult(BaseModel):
    """Structured output persisted on the AgentRun when discovery succeeds.

    Every counter has one documented meaning and no candidate is counted
    twice:

    * ``searched`` — raw search hits returned across every executed query,
      before deduplication.
    * ``malformed_results`` — search hits dropped for a non-http URL or an
      empty canonical form; they never become candidates.
    * ``duplicates_skipped`` — further hits whose canonical URL was already
      seen in this run; they never become candidates.
    *       ``candidates_found`` — the deduplicated, validated candidate URLs that
      entered the crawl phase (capped at ``max_candidates``). Each candidate
      is resolved by exactly one terminal outcome, giving the invariant
      ``jobs_created + jobs_updated + jobs_skipped + listings_skipped +
      invalid_results == candidates_found``.
    * ``invalid_results`` — candidates rejected because their URL does not
      fit the database columns.
    * ``listings_skipped`` — candidates whose URL or crawled page is a
      listing, not an individual vacancy.
    * ``scrape_attempts`` — candidates actually sent to the crawler.
      ``scrape_attempts == extractions_succeeded + scrape_failures``: every
      attempt either yields usable content or fails.
    * ``extractions_succeeded`` — crawls that returned usable content,
      counted regardless of whether the candidate is later rejected by
      role/location filtering or listing-page classification.
    * ``scrape_failures`` — crawls that errored or returned no usable
      content; a failed crawl may still persist from search-derived fields.
    * ``jobs_created`` / ``jobs_updated`` — persisted candidate jobs inserted
      or matched by ``canonical_url``.

    Each candidate skipped or degraded for any reason also appears in
    ``issues``, which is capped at :data:`MAX_DISCOVERY_ISSUES`; the counters
    stay exact even when the detail list is truncated.
    """

    model_config = ConfigDict(extra="forbid")

    query: dict[str, str]
    queries: list[str] = Field(default_factory=list)
    searched: int = Field(ge=0)
    candidates_found: int = Field(ge=0)
    jobs_created: int = Field(ge=0)
    jobs_updated: int = Field(ge=0)
    jobs_skipped: int = Field(ge=0)
    errors: list[str]
    job_ids: list[str]
    duplicates_skipped: int = Field(default=0, ge=0)
    scrape_attempts: int = Field(default=0, ge=0)
    extractions_succeeded: int = Field(default=0, ge=0)
    listings_skipped: int = Field(default=0, ge=0)
    invalid_results: int = Field(default=0, ge=0)
    malformed_results: int = Field(default=0, ge=0)
    scrape_failures: int = Field(default=0, ge=0)
    issues: list[DiscoveryIssue] = Field(default_factory=list)


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def discovery_idempotency_key(request: DiscoveryRequest) -> str:
    """Return the deterministic idempotency key for a request.

    Fields are trimmed, internally whitespace-collapsed and casefolded
    first, so requests differing only in case or spacing share one key:
    ``job_discovery:{role}:{location}:{experience}``.
    """
    return ":".join(
        (
            DISCOVERY_AGENT_NAME,
            _normalized(request.role),
            _normalized(request.location),
            _normalized(request.experience),
        )
    )
