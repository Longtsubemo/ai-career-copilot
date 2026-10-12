from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column_type, vocabulary_check
from app.models.enums import RunStatus

if TYPE_CHECKING:
    from app.models.application import Application
    from app.models.candidate import Candidate
    from app.models.document import Document
    from app.models.job import Job


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable execution state for a single agent invocation.

    All agents share this one table; ``agent_name`` is a discriminator, not a
    separate entity. ``idempotency_key`` makes a re-triggered pipeline safe: a
    partial unique index permits any number of failed attempts but only one
    successful run per key.

    ``ix_agent_runs_claim`` is shaped for ``SELECT ... FOR UPDATE SKIP LOCKED``
    so PostgreSQL can act as the work queue without any additional broker.
    """

    __tablename__ = "agent_runs"

    __table_args__ = (
        Index("ix_agent_runs_claim", "status", "created_at"),
        Index(
            "uq_agent_runs_idempotency_succeeded",
            "idempotency_key",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
        vocabulary_check("status", RunStatus, "status_vocabulary"),
    )

    agent_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[RunStatus] = mapped_column(
        enum_column_type(RunStatus),
        nullable=False,
        default=RunStatus.pending,
        server_default=RunStatus.pending.value,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    input: Mapped[dict | None] = mapped_column(JSONB)
    output: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default="3",
    )

    job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    candidate_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job | None] = relationship(back_populates="agent_runs")
    candidate: Mapped[Candidate | None] = relationship(back_populates="agent_runs")
    documents: Mapped[list[Document]] = relationship(back_populates="agent_run")