from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun
    from app.models.application import Application
    from app.models.document import Document
    from app.models.match import Match


class Candidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The person the copilot works for.

    The local application profile is the durable identity for private data.
    Older profiles remain intact during an upgrade; exactly one profile may
    be selected for normal application use.
    """

    __tablename__ = "candidates"
    __table_args__ = (
        Index(
            "uq_candidates_single_local_profile",
            "is_local_profile",
            unique=True,
            postgresql_where=text("is_local_profile IS TRUE"),
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_local_profile: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    headline: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    years_of_experience: Mapped[float | None] = mapped_column(Float)
    seniority: Mapped[str | None] = mapped_column(String(100))
    skills: Mapped[list[str] | None] = mapped_column(JSONB)
    summary: Mapped[str | None] = mapped_column(Text)

    documents: Mapped[list[Document]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    matches: Mapped[list[Match]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    applications: Mapped[list[Application]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    agent_runs: Mapped[list[AgentRun]] = relationship(
        back_populates="candidate",
        passive_deletes=True,
    )
