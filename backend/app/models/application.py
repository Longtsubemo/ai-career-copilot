from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column_type, vocabulary_check
from app.models.enums import ApplicationStatus

if TYPE_CHECKING:
    from app.models.candidate import Candidate
    from app.models.document import Document
    from app.models.job import Job


class Application(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A submitted application, treated as a historical record.

    ``candidate_id`` is required: an application belongs to exactly one
    candidate profile and is never visible to another account. A job may be
    applied to more than once, so ``job_id`` is not unique. The ``SET NULL``
    foreign key plus the title and company snapshots guarantee that cleaning up
    discovered jobs can never erase the fact of a submission.
    """

    __tablename__ = "applications"

    __table_args__ = (
        vocabulary_check("status", ApplicationStatus, "status_vocabulary"),
    )

    candidate_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status: Mapped[ApplicationStatus] = mapped_column(
        enum_column_type(ApplicationStatus),
        nullable=False,
        default=ApplicationStatus.draft,
        server_default=ApplicationStatus.draft.value,
        index=True,
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_title_snapshot: Mapped[str | None] = mapped_column(String(255))
    company_name_snapshot: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    candidate: Mapped[Candidate] = relationship(back_populates="applications")
    job: Mapped[Job | None] = relationship(back_populates="applications")
    documents: Mapped[list[Document]] = relationship(back_populates="application")