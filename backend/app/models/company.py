from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.job import Job


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A discovered employer.

    ``normalized_name`` is the deterministic dedupe key produced by
    :func:`app.services.normalization.normalize_company_name`. Deleting a
    company never deletes its jobs; the ``SET NULL`` foreign key detaches them.
    """

    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    website: Mapped[str | None] = mapped_column(String(500))
    industry: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))

    jobs: Mapped[list[Job]] = relationship(
        back_populates="company",
        passive_deletes=True,
    )