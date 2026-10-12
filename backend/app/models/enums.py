"""Controlled vocabularies shared by database models and application services.

These values are deterministic: they drive workflow transitions and must never
be produced by an LLM. Free-form model output is stored in JSONB payloads and is
never interpreted as a control-flow value.
"""

from enum import StrEnum


class CrawlStatus(StrEnum):
    """Lifecycle of a discovered job posting's crawl."""

    pending = "pending"
    scraped = "scraped"
    failed = "failed"


class AvailabilityStatus(StrEnum):
    """Whether a discovered vacancy is currently open.

    This is deliberately separate from :class:`CrawlStatus`: a crawl can
    fail while the vacancy is still open, and a successful crawl of a page
    that says the role is filled means the vacancy is closed. ``stale`` is
    an age-based reading of ``available`` and is derived at read time, never
    persisted — a stale posting is not known to be closed.
    """

    unknown = "unknown"
    available = "available"
    stale = "stale"
    closed = "closed"


class RunStatus(StrEnum):
    """Lifecycle of an agent run and of agent-produced artifacts."""

    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class DocumentKind(StrEnum):
    """Kinds of candidate-owned documents."""

    master_resume = "master_resume"
    tailored_resume = "tailored_resume"
    cover_letter = "cover_letter"


class ApplicationStatus(StrEnum):
    """Lifecycle of a submitted application."""

    draft = "draft"
    applied = "applied"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"