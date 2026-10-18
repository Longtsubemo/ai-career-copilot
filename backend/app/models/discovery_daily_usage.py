from __future__ import annotations

from datetime import date

from sqlalchemy import CheckConstraint, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DiscoveryDailyUsage(Base):
    """Atomic per-day counters for authenticated provider-backed operations.

    Each discovery run or job-verification attempt reserves one unit.
    ``scope_key`` is either ``global`` or an authenticated user's UUID
    prefixed with ``user:``. Rows for earlier UTC days are pruned when a new
    operation is reserved; no request identity or forwarded IP is stored.
    """

    __tablename__ = "discovery_daily_usage"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
    )

    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


__all__ = ["DiscoveryDailyUsage"]
