"""Offline tests for UTC quota boundaries and PostgreSQL upsert shape."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from app.services.discovery_quota import _increment_statement, utc_day


def test_utc_day_uses_utc_not_the_callers_local_calendar_date() -> None:
    india = timezone(timedelta(hours=5, minutes=30))
    before_utc_midnight = datetime(2026, 10, 17, 0, 15, tzinfo=india)
    after_utc_midnight = datetime(2026, 10, 17, 6, 0, tzinfo=india)

    assert utc_day(before_utc_midnight).isoformat() == "2026-10-16"
    assert utc_day(after_utc_midnight).isoformat() == "2026-10-17"


def test_naive_quota_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        utc_day(datetime(2026, 10, 16))


def test_quota_counter_uses_conditional_postgresql_upsert() -> None:
    statement = _increment_statement(
        day=utc_day(datetime(2026, 10, 16, tzinfo=timezone.utc)),
        scope_key="user:00000000-0000-0000-0000-000000000001",
        limit=3,
    )
    sql = str(statement.compile(dialect=postgresql.dialect())).lower()

    assert "on conflict (usage_date, scope_key) do update" in sql
    assert "discovery_daily_usage.attempts +" in sql
    assert "where discovery_daily_usage.attempts <" in sql
    assert "returning discovery_daily_usage.attempts" in sql
