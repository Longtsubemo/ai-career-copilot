from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun
    from app.models.application import Application
    from app.models.document import Document
    from app.models.match import Match
    from app.models.user import User


class Candidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The person the copilot works for.

    ``user_id`` is the owner: required and unique, so every candidate profile
    belongs to exactly one account and no profile can be ownerless. The name
    still carries no uniqueness constraint — a name is not an identity.
    """

    __tablename__ = "candidates"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    user: Mapped[User] = relationship(back_populates="candidate")
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