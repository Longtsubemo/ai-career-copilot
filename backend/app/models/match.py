from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.candidate import Candidate
    from app.models.document import Document
    from app.models.job import Job
    from app.models.job_analysis import JobAnalysis


class Match(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A candidate/job match score produced by the matching agent.

    ``resume_document_id`` and ``job_analysis_id`` are both mandatory so that
    any score can be reproduced from the exact inputs that produced it, and so
    that matching cannot run before a job has been analysed.
    """

    __tablename__ = "matches"

    __table_args__ = (
        Index(
            "uq_matches_job_resume_prompt",
            "job_id",
            "resume_document_id",
            "prompt_version",
            unique=True,
        ),
        CheckConstraint("score >= 0 AND score <= 100", name="score_range"),
    )

    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resume_document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_analysis_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("job_analyses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    verdict: Mapped[dict | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)

    job: Mapped[Job] = relationship(back_populates="matches")
    candidate: Mapped[Candidate] = relationship(back_populates="matches")
    resume_document: Mapped[Document] = relationship(back_populates="matches")
    job_analysis: Mapped[JobAnalysis] = relationship(back_populates="matches")