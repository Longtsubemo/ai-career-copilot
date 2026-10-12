"""Age-based availability resolution.

``stale`` is derived at read time; the model only ever stores
``unknown``/``available``/``closed``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.enums import AvailabilityStatus
from app.services.freshness import (
    FRESHNESS_DAYS_DEFAULT,
    freshness_threshold,
    resolve_availability,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_recent_available_stays_available() -> None:
    assert (
        resolve_availability(
            AvailabilityStatus.available,
            NOW - timedelta(days=3),
            now=NOW,
            threshold_days=30,
        )
        is AvailabilityStatus.available
    )


def test_old_available_becomes_stale() -> None:
    assert (
        resolve_availability(
            AvailabilityStatus.available,
            NOW - timedelta(days=31),
            now=NOW,
            threshold_days=30,
        )
        is AvailabilityStatus.stale
    )


def test_exact_threshold_is_still_fresh() -> None:
    assert (
        resolve_availability(
            AvailabilityStatus.available,
            NOW - timedelta(days=30),
            now=NOW,
            threshold_days=30,
        )
        is AvailabilityStatus.available
    )


def test_available_without_timestamp_stays_available() -> None:
    assert (
        resolve_availability(
            AvailabilityStatus.available, None, now=NOW, threshold_days=1
        )
        is AvailabilityStatus.available
    )


@pytest.mark.parametrize(
    "status", [AvailabilityStatus.unknown, AvailabilityStatus.closed]
)
def test_unknown_and_closed_are_never_aged(status: AvailabilityStatus) -> None:
    assert (
        resolve_availability(
            status,
            NOW - timedelta(days=9999),
            now=NOW,
            threshold_days=30,
        )
        is status
    )


def test_naive_timestamp_is_interpreted_as_utc() -> None:
    naive_now = datetime(2026, 6, 1, 12, 0)
    assert (
        resolve_availability(
            AvailabilityStatus.available,
            naive_now - timedelta(days=40),
            now=naive_now,
            threshold_days=30,
        )
        is AvailabilityStatus.stale
    )


def test_default_threshold_is_thirty_days() -> None:
    assert FRESHNESS_DAYS_DEFAULT == 30


def test_invalid_threshold_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_availability(
            AvailabilityStatus.available, NOW, now=NOW, threshold_days=0
        )


def test_freshness_threshold_subtracts_days() -> None:
    assert freshness_threshold(now=NOW, days=7) == NOW - timedelta(days=7)
    with pytest.raises(ValueError):
        freshness_threshold(now=NOW, days=0)
