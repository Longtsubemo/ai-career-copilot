"""Fixtures for the HTTP API test suite.

Every API test drives real routes against the same savepoint-backed
PostgreSQL session the rest of the suite uses: the app's ``get_db``
dependency is overridden to yield that session, so writes are visible to
the test and rolled back with the fixture. Each test builds its own app,
which keeps injected fakes and the per-app handler registry from leaking
between tests. Nothing in this suite opens a socket.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import create_app


def _session_override(session: Session) -> Any:
    def override_get_db() -> Iterator[Session]:
        yield session

    return override_get_db


def make_client(
    session: Session,
    *,
    searxng: object | None = None,
    firecrawl: object | None = None,
    gemini: object | None = None,
    raise_server_exceptions: bool = True,
) -> TestClient:
    """Build a TestClient whose app uses the test session and any fakes."""
    app = create_app(searxng=searxng, firecrawl=firecrawl, gemini=gemini)
    app.dependency_overrides[get_db] = _session_override(session)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


@pytest.fixture
def client_factory(session: Session):
    """Return a callable building a fresh app-backed TestClient."""

    def factory(**kwargs: object) -> TestClient:
        return make_client(session, **kwargs)  # type: ignore[arg-type]

    return factory


@pytest.fixture
def client(client_factory) -> TestClient:
    """A client for routes that never reach an external service.

    Candidate, resume, job, application and error routes need no fakes;
    anything that runs an agent passes its own through ``client_factory``.
    """
    return client_factory()


__all__ = ["make_client"]
