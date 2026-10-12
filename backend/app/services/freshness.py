"""Deterministic freshness interpretation for stored job availability.

The database stores only what discovery actually established: ``unknown``,
``available`` or ``closed``. ``stale`` is an age-based reading of an
``available`` job and is computed at read time, so a posting can age out of
"recently verified" without a scheduler, background worker or recurring
crawl. A stale posting is explicitly *not* closed — nothing was observed to
close it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.enums import AvailabilityStatus

FRESHNESS_DAYS_DEFAULT = 30


def freshness_threshold(*, now: datetime, days: int) -> datetime:
    """Return the oldest ``last_verified_at`` still considered fresh."""
    if days < 1:
        raise ValueError("days must be >= 1")
    return _as_aware(now) - timedelta(days=days)


def resolve_availability(
    availability: AvailabilityStatus,
    last_verified_at: datetime | None,
    *,
    now: datetime,
    threshold_days: int = FRESHNESS_DAYS_DEFAULT,
) -> AvailabilityStatus:
    """Return the effective, read-time availability of a job.

    Only a stored ``available`` can become ``stale``. ``unknown`` and
    ``closed`` are returned unchanged, and an ``available`` job with no
    verification timestamp is treated as fresh (it was set by a successful
    crawl that also stamped ``crawled_at``).
    """
    if threshold_days < 1:
        raise ValueError("threshold_days must be >= 1")
    if availability is not AvailabilityStatus.available or last_verified_at is None:
        return availability
    if _as_aware(now) - _as_aware(last_verified_at) > timedelta(days=threshold_days):
        return AvailabilityStatus.stale
    return availability


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


__all__ = [
    "FRESHNESS_DAYS_DEFAULT",
    "freshness_threshold",
    "resolve_availability",
]
