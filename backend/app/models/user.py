from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.auth_session import AuthSession
    from app.models.candidate import Candidate


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An authenticated account.

    The product was single-user; this is the identity every private row will
    eventually hang off. ``email`` is stored normalized (lower-cased, trimmed)
    and unique so two accounts cannot share a login. Password material is only
    ever a verified hash — this model never holds a plaintext credential.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    candidate: Mapped[Candidate | None] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
