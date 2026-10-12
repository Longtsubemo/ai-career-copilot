from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column_type, vocabulary_check
from app.models.enums import DocumentKind

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun
    from app.models.application import Application
    from app.models.candidate import Candidate
    from app.models.job import Job
    from app.models.match import Match


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An immutable, append-only candidate document.

    Generations are never updated in place: a regenerated resume or cover letter
    is a new row pointing at the same ``source_document_id``, so every version
    stays comparable. Deleting a job, application or source document detaches
    the reference instead of destroying the artifact.
    """

    __tablename__ = "documents"

    __table_args__ = (
        vocabulary_check("kind", DocumentKind, "kind_vocabulary"),
        CheckConstraint(
            "kind <> 'tailored_resume' OR source_document_id IS NOT NULL",
            name="tailored_resume_requires_source",
        ),
    )

    kind: Mapped[DocumentKind] = mapped_column(
        enum_column_type(DocumentKind),
        nullable=False,
        index=True,
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
    source_document_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    application_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("applications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(100))

    candidate: Mapped[Candidate] = relationship(back_populates="documents")
    job: Mapped[Job | None] = relationship(back_populates="documents")
    application: Mapped[Application | None] = relationship(back_populates="documents")
    agent_run: Mapped[AgentRun | None] = relationship(back_populates="documents")
    source_document: Mapped[Document | None] = relationship(
        remote_side="Document.id",
        foreign_keys=[source_document_id],
        back_populates="derived_documents",
    )
    derived_documents: Mapped[list[Document]] = relationship(
        foreign_keys=[source_document_id],
        back_populates="source_document",
    )
    matches: Mapped[list[Match]] = relationship(
        back_populates="resume_document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )