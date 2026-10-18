"""PostgreSQL concurrency checks for the daily quota upsert."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from app.models.discovery_daily_usage import DiscoveryDailyUsage
from app.services.discovery_quota import (
    DailyProviderLimitExceeded,
    reserve_provider_attempt,
)


def test_concurrent_provider_reservations_never_exceed_limits(engine) -> None:
    user_id = uuid4()
    per_user_time = datetime(2099, 1, 1, tzinfo=timezone.utc)
    global_time = datetime(2099, 1, 2, tzinfo=timezone.utc)
    session_factory = sessionmaker(bind=engine)

    def reserve(
        *, user: UUID, now: datetime, user_limit: int, global_limit: int
    ) -> bool:
        with session_factory() as session:
            try:
                reserve_provider_attempt(
                    session,
                    user_id=user,
                    user_limit=user_limit,
                    global_limit=global_limit,
                    now=now,
                )
                session.commit()
                return True
            except DailyProviderLimitExceeded:
                return False

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            per_user_results = list(
                pool.map(
                    lambda _: reserve(
                        user=user_id,
                        now=per_user_time,
                        user_limit=3,
                        global_limit=20,
                    ),
                    range(8),
                )
            )
        assert sum(per_user_results) == 3

        user_ids = [uuid4() for _ in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            global_results = list(
                pool.map(
                    lambda user: reserve(
                        user=user,
                        now=global_time,
                        user_limit=10,
                        global_limit=3,
                    ),
                    user_ids,
                )
            )
        assert sum(global_results) == 3
    finally:
        with Session(engine) as cleanup:
            cleanup.execute(
                delete(DiscoveryDailyUsage).where(
                    DiscoveryDailyUsage.usage_date.in_(
                        [per_user_time.date(), global_time.date()]
                    )
                )
            )
            cleanup.commit()
