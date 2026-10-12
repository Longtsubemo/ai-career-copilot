from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column_type, vocabulary_check
from app.models.enums import AvailabilityStatus, CrawlStatus

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun
    from app.models.application import Application
    from app.models.company import Company
    from app.models.document import Document
    from app.models.job_analysis import JobAnalysis
    from app.models.match import Match


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A discovered job posting.

    A row is created from a search result before any crawling happens, so
    ``description`` is nullable and ``crawl_status`` starts at ``pending``.
    ``canonical_url`` is the dedupe key; ``job_url`` is kept verbatim as
    provenance for the URL that was actually discovered.

    ``crawl_status`` records the technical crawl outcome, while
    ``availability`` records what is known about the vacancy itself. The two
    are independent: a failed crawl must not close a vacancy, and a page
    that reports the role is filled closes it even though the crawl
    succeeded. ``crawled_at`` doubles as the last credible verification
    timestamp and only advances on a successful crawl of an individual
    vacancy; ``closed_at`` stamps affirmative closure evidence.
    """

    __tablename__ = "jobs"

    __table_args__ = (
        vocabulary_check("crawl_status", CrawlStatus, "crawl_status_vocabulary"),
        vocabulary_check(
            "availability", AvailabilityStatus, "availability_vocabulary"
        ),
    )

    company_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(255), index=True)
    employment_type: Mapped[str | None] = mapped_column(String(100))
    experience_level: Mapped[str | None] = mapped_column(String(100))

    job_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    date_posted: Mapped[date | None] = mapped_column(Date, index=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )

    crawl_status: Mapped[CrawlStatus] = mapped_column(
        enum_column_type(CrawlStatus),
        nullable=False,
        default=CrawlStatus.pending,
        server_default=CrawlStatus.pending.value,
        index=True,
    )
    crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    availability: Mapped[AvailabilityStatus] = mapped_column(
        enum_column_type(AvailabilityStatus),
        nullable=False,
        default=AvailabilityStatus.unknown,
        server_default=AvailabilityStatus.unknown.value,
        index=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    company: Mapped[Company | None] = relationship(back_populates="jobs")
    applications: Mapped[list[Application]] = relationship(
        back_populates="job",
        passive_deletes=True,
    )
    analyses: Mapped[list[JobAnalysis]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    matches: Mapped[list[Match]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="job",
        passive_deletes=True,
    )
    agent_runs: Mapped[list[AgentRun]] = relationship(
        back_populates="job",
        passive_deletes=True,
    )