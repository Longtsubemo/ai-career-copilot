"""Deterministic reads over discovered jobs.

Filtering is plain SQL over columns the discovery agent already wrote:
location is a case-insensitive substring match, experience level and
source are exact, and the active flag is a boolean. No salary, currency,
semantic or vector search happens here — those were removed from scope.

The availability filter mirrors the read-time semantics of
:func:`app.services.freshness.resolve_availability`: ``available`` means a
recently-verified open vacancy, ``stale`` means a previously verified one
that aged past the threshold, while ``closed`` and ``unknown`` are direct
column matches. The caller supplies the same ``now`` the response renders
with, so a row at the freshness boundary is not filtered one way and
labelled another.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.enums import AvailabilityStatus
from app.models.job import Job

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def _contains(pattern: str) -> str:
    escaped = (
        pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def availability_predicates(
    availability: AvailabilityStatus,
    *,
    now: datetime,
    threshold_days: int,
) -> list[object]:
    """Return the SQL predicates that isolate one effective availability.

    ``stale`` and ``available`` are the two halves of a stored ``available``:
    fresh rows have a verification at or after the cutoff, stale rows have one
    before it. Rows whose availability is the stored value ``available`` but
    carry no verification timestamp are treated as available (fresh), exactly
    like :func:`resolve_availability`.
    """
    if availability is AvailabilityStatus.closed:
        return [Job.availability == AvailabilityStatus.closed]
    if availability is AvailabilityStatus.unknown:
        return [Job.availability == AvailabilityStatus.unknown]
    if threshold_days < 1:
        raise ValueError("threshold_days must be >= 1")
    if now.tzinfo is None:
        cutoff = now.replace(tzinfo=timezone.utc) - timedelta(days=threshold_days)
    else:
        cutoff = now - timedelta(days=threshold_days)
    base = [Job.availability == AvailabilityStatus.available, Job.crawled_at.is_not(None)]
    if availability is AvailabilityStatus.stale:
        return base + [Job.crawled_at < cutoff]
    return base + [Job.crawled_at >= cutoff]


def get_job(session: Session, job_id: UUID) -> Job | None:
    """Return one job by id, or None."""
    return session.get(Job, job_id)


def list_jobs(
    session: Session,
    *,
    location: str | None = None,
    experience_level: str | None = None,
    is_active: bool | None = None,
    source: str | None = None,
    availability: AvailabilityStatus | None = None,
    now: datetime,
    threshold_days: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> tuple[list[Job], int]:
    """Return one page of jobs plus the total number of matches.

    Deterministic ordering: most recently created first, with the id as a
    tie-break so pagination never repeats or skips a row. The availability
    filter is applied before the total is counted, so ``total`` and the page
    both describe the filtered set.
    """
    filters = []
    if location:
        filters.append(Job.location.ilike(_contains(location), escape="\\"))
    if experience_level:
        filters.append(Job.experience_level == experience_level)
    if is_active is not None:
        filters.append(Job.is_active == is_active)
    if source:
        filters.append(Job.source == source)
    if availability is not None:
        filters.extend(
            availability_predicates(availability, now=now, threshold_days=threshold_days)
        )

    total = session.scalar(select(func.count()).select_from(Job).where(*filters)) or 0
    stmt = (
        select(Job)
        .where(*filters)
        .options(selectinload(Job.company))
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = list(session.execute(stmt).scalars().all())
    return items, total
