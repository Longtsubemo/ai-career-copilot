"""PostgreSQL concurrency checks for local and shared quota upserts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.models.discovery_daily_usage import DiscoveryDailyUsage
from app.services.discovery_quota import (
    DailyProviderLimitExceeded,
    reserve_provider_attempt,
)


def test_concurrent_provider_reservations_never_exceed_local_or_global_limits(
    engine,
) -> None:
    local_time = datetime(2099, 1, 1, tzinfo=timezone.utc)
    global_time = datetime(2099, 1, 2, tzinfo=timezone.utc)
    session_factory = sessionmaker(bind=engine)

    def reserve(*, now: datetime, local_limit: int, global_limit: int) -> bool:
        with session_factory() as session:
            try:
                reserve_provider_attempt(
                    session,
                    local_limit=local_limit,
                    global_limit=global_limit,
                    now=now,
                )
                session.commit()
                return True
            except DailyProviderLimitExceeded:
                return False

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            local_results = list(
                pool.map(
                    lambda _: reserve(
                        now=local_time, local_limit=3, global_limit=20
                    ),
                    range(8),
                )
            )
        assert sum(local_results) == 3

        with ThreadPoolExecutor(max_workers=8) as pool:
            global_results = list(
                pool.map(
                    lambda _: reserve(
                        now=global_time, local_limit=20, global_limit=3
                    ),
                    range(8),
                )
            )
        assert sum(global_results) == 3
    finally:
        with Session(engine) as cleanup:
            cleanup.execute(
                delete(DiscoveryDailyUsage).where(
                    DiscoveryDailyUsage.usage_date.in_(
                        [local_time.date(), global_time.date()]
                    )
                )
            )
            cleanup.commit()
