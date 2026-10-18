"""Durable UTC-day admission limits for provider-backed operations."""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.models.discovery_daily_usage import DiscoveryDailyUsage

GLOBAL_SCOPE = "global"


class DailyProviderLimitExceeded(RuntimeError):
    """The per-user or global daily provider-operation budget is exhausted."""


# Kept as an alias for callers that used the original discovery-only name.
DiscoveryDailyLimitExceeded = DailyProviderLimitExceeded


def utc_day(now: datetime | None = None) -> date:
    """Return the calendar day at UTC, regardless of the input timezone."""
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("quota timestamps must be timezone-aware")
    return instant.astimezone(timezone.utc).date()


def _increment_statement(*, day: date, scope_key: str, limit: int):
    table = DiscoveryDailyUsage.__table__
    return (
        postgresql_insert(DiscoveryDailyUsage)
        .values(usage_date=day, scope_key=scope_key, attempts=1)
        .on_conflict_do_update(
            index_elements=[table.c.usage_date, table.c.scope_key],
            set_={"attempts": table.c.attempts + 1},
            where=table.c.attempts < limit,
        )
        .returning(table.c.attempts)
    )


def _increment_if_below(
    session: Session, *, day: date, scope_key: str, limit: int
) -> int | None:
    statement = _increment_statement(day=day, scope_key=scope_key, limit=limit)
    return session.execute(statement).scalar_one_or_none()


def reserve_provider_attempt(
    session: Session,
    *,
    user_id: UUID,
    user_limit: int,
    global_limit: int,
    now: datetime | None = None,
) -> date:
    """Reserve one provider-backed operation in both counters.

    PostgreSQL's unique-key ``ON CONFLICT`` update serializes concurrent
    increments on each counter row. If either limit is reached, rolling back
    removes both increments. The caller must commit before invoking a search
    or scrape provider so a failed/aborted operation still consumes the attempt.

    One discovery run reserves one unit, even when that run makes multiple
    bounded provider calls. Each explicit job-verification operation reserves
    one unit. These counters limit application operations rather than exact
    HTTP request volume.
    """
    if user_limit < 1 or global_limit < 1:
        raise ValueError("provider-operation limits must be positive")
    day = utc_day(now)
    try:
        session.execute(
            delete(DiscoveryDailyUsage).where(
                DiscoveryDailyUsage.usage_date < day
            )
        )
        user_attempts = _increment_if_below(
            session,
            day=day,
            scope_key=f"user:{user_id}",
            limit=user_limit,
        )
        if user_attempts is None:
            raise DailyProviderLimitExceeded
        global_attempts = _increment_if_below(
            session,
            day=day,
            scope_key=GLOBAL_SCOPE,
            limit=global_limit,
        )
        if global_attempts is None:
            raise DailyProviderLimitExceeded
        return day
    except DailyProviderLimitExceeded:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise


__all__ = [
    "DiscoveryDailyLimitExceeded",
    "DailyProviderLimitExceeded",
    "GLOBAL_SCOPE",
    "reserve_provider_attempt",
    "utc_day",
]


# Backward-compatible name for existing discovery callers and tests.
reserve_discovery_attempt = reserve_provider_attempt
