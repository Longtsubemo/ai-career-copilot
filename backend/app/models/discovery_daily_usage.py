from __future__ import annotations

from datetime import date

from sqlalchemy import CheckConstraint, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DiscoveryDailyUsage(Base):
    """Atomic per-day counters for local provider-backed operations.

    The local installation and shared global scopes each reserve one unit per
    discovery run or job-verification attempt. Earlier UTC days are pruned
    when a new operation is reserved.
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
