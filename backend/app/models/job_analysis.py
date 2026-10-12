from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column_type, vocabulary_check
from app.models.enums import RunStatus

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.match import Match


class JobAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Structured extraction produced by the job analysis agent.

    ``analysis`` is JSONB rather than typed columns because the extraction
    schema is expected to change as prompts are tuned; keeping it as JSONB means
    schema iteration needs no migration. ``status`` is what drives workflow and
    is deliberately independent of the model's own confidence.
    """

    __tablename__ = "job_analyses"

    __table_args__ = (
        Index(
            "uq_job_analyses_job_prompt_succeeded",
            "job_id",
            "prompt_version",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
        vocabulary_check("status", RunStatus, "status_vocabulary"),
    )

    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    analysis: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        enum_column_type(RunStatus),
        nullable=False,
        default=RunStatus.pending,
        server_default=RunStatus.pending.value,
        index=True,
    )

    job: Mapped[Job] = relationship(back_populates="analyses")
    matches: Mapped[list[Match]] = relationship(
        back_populates="job_analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )