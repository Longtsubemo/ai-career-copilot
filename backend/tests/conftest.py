"""Shared pytest fixtures.

Tests run against a real PostgreSQL database because the guarantees under test
are PostgreSQL behaviours: partial unique indexes, ``ON DELETE SET NULL`` and
``FOR UPDATE SKIP LOCKED``. A SQLite substitute would silently not exercise them.

The database name is derived from ``TEST_DATABASE_URL`` or, failing that, from
the configured application URL with a ``_test`` suffix, so tests never touch the
development database.
"""

import os
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  imported for its metadata side effects
from app.core.config import get_settings
from app.db.base import Base

MAINTENANCE_DATABASE = "postgres"


def resolve_test_database_url() -> str:
    """Return the URL of the database tests may create and drop freely."""
    explicit = os.getenv("TEST_DATABASE_URL")
    if explicit:
        return explicit

    parts = urlsplit(get_settings().database_url)
    name = parts.path.lstrip("/") or "career_copilot"
    if not name.endswith("_test"):
        name = f"{name}_test"
    return urlunsplit(parts._replace(path=f"/{name}"))


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = resolve_test_database_url()
    parts = urlsplit(url)
    target = parts.path.lstrip("/")

    admin_url = urlunsplit(parts._replace(path=f"/{MAINTENANCE_DATABASE}"))
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target},
            ).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{target}"'))
    finally:
        admin_engine.dispose()

    yield url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    session_engine = create_engine(database_url, poolclass=NullPool)
    Base.metadata.drop_all(session_engine)
    Base.metadata.create_all(session_engine)
    yield session_engine
    Base.metadata.drop_all(session_engine)
    session_engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Session bound to a transaction that is always rolled back.

    ``create_savepoint`` lets a test call ``commit()`` and still be isolated
    from the next test.
    """
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()