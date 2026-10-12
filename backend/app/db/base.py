from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import Uuid

naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    metadata = MetaData(naming_convention=naming_convention)


class UUIDPrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def enum_column_type(enum_class: type[StrEnum], length: int = 50) -> Enum:
    """Build a VARCHAR-backed SQLAlchemy enum for a controlled vocabulary.

    ``native_enum=False`` is deliberate: PostgreSQL native enums cannot be
    altered or have values removed without ``ALTER TYPE`` gymnastics, and these
    vocabularies are expected to grow. The database constraint is declared
    separately via :func:`vocabulary_check` because the ``ck`` entry of
    :data:`naming_convention` requires an explicit constraint name.
    """
    return Enum(enum_class, native_enum=False, create_constraint=False, length=length)


def vocabulary_check(
    column_name: str,
    enum_class: type[StrEnum],
    constraint_name: str,
) -> CheckConstraint:
    """Build a named CHECK constraint restricting a column to enum values.

    ``constraint_name`` must omit the ``ck_<table>_`` prefix: the ``ck`` entry of
    :data:`naming_convention` is applied on top of whatever name is given here.
    """
    allowed = ", ".join(f"'{member.value}'" for member in enum_class)
    return CheckConstraint(f"{column_name} IN ({allowed})", name=constraint_name)
