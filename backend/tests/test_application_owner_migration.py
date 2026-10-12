"""The application-owner migration (0005) must guard and enforce ownership.

Revision 0005 adds ``applications.candidate_id`` to a table that predates
ownership. These tests prove the guard: a single candidate backfills every
existing row, several candidates abort as ambiguous, no candidate aborts, and
a downgrade removes the column again. Each test runs against its own throwaway
database created and dropped through the maintenance connection.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

BACKEND_ROOT = Path(__file__).resolve().parents[1]

REVISION_BEFORE = "20261012_0004"
REVISION_AFTER = "20261012_0005"


def build_alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT))
    config.cmd_opts = Namespace(x=[f"database_url={url}"])
    return config


def _admin_engine(database_url: str) -> Engine:
    parts = urlsplit(database_url)
    admin_url = urlunsplit(parts._replace(path="/postgres"))
    return create_engine(admin_url, isolation_level="AUTOCOMMIT")


def _create_database(database_url: str, suffix: str) -> tuple[str, str]:
    parts = urlsplit(database_url)
    base_name = parts.path.lstrip("/") or "career_copilot"
    target = f"{base_name}_{suffix}"
    target_url = urlunsplit(parts._replace(path=f"/{target}"))
    engine = _admin_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{target}"'))
    finally:
        engine.dispose()
    return target, target_url


def _drop_database(database_url: str, target: str) -> None:
    engine = _admin_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)'))
    finally:
        engine.dispose()


def _insert_candidate(engine: Engine, email: str) -> str:
    user_id = str(uuid4())
    candidate_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_active, created_at, updated_at) "
                "VALUES (:id, :email, 'hash', true, now(), now())"
            ),
            {"id": user_id, "email": email},
        )
        connection.execute(
            text(
                "INSERT INTO candidates (id, name, user_id, created_at, updated_at) "
                "VALUES (:id, 'Owner', :user_id, now(), now())"
            ),
            {"id": candidate_id, "user_id": user_id},
        )
    return candidate_id


def _insert_application(engine: Engine, notes: str = "legacy") -> str:
    application_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO applications (id, status, notes, created_at, updated_at) "
                "VALUES (:id, 'draft', :notes, now(), now())"
            ),
            {"id": application_id, "notes": notes},
        )
    return application_id


def test_upgrade_backfills_every_row_when_one_candidate_exists(
    database_url: str,
) -> None:
    target, url = _create_database(database_url, "app_owned")
    try:
        config = build_alembic_config(url)
        command.upgrade(config, REVISION_BEFORE)

        engine = create_engine(url, poolclass=NullPool)
        candidate_id = _insert_candidate(engine, "owner@example.com")
        application_id = _insert_application(engine)
        engine.dispose()

        command.upgrade(config, REVISION_AFTER)

        engine = create_engine(url, poolclass=NullPool)
        try:
            with engine.connect() as connection:
                owner = connection.execute(
                    text("SELECT candidate_id FROM applications WHERE id = :id"),
                    {"id": application_id},
                ).scalar_one()
            assert str(owner) == candidate_id

            columns = {
                c["name"]: c for c in inspect(engine).get_columns("applications")
            }
            assert columns["candidate_id"]["nullable"] is False
        finally:
            engine.dispose()
    finally:
        _drop_database(database_url, target)


def test_upgrade_aborts_when_ownership_is_ambiguous(database_url: str) -> None:
    target, url = _create_database(database_url, "app_ambiguous")
    try:
        config = build_alembic_config(url)
        command.upgrade(config, REVISION_BEFORE)

        engine = create_engine(url, poolclass=NullPool)
        _insert_candidate(engine, "first@example.com")
        _insert_candidate(engine, "second@example.com")
        _insert_application(engine)
        engine.dispose()

        with pytest.raises(RuntimeError, match="ambiguous"):
            command.upgrade(config, REVISION_AFTER)
    finally:
        _drop_database(database_url, target)


def test_upgrade_aborts_when_no_candidate_can_own_the_rows(
    database_url: str,
) -> None:
    target, url = _create_database(database_url, "app_orphaned")
    try:
        config = build_alembic_config(url)
        command.upgrade(config, REVISION_BEFORE)

        engine = create_engine(url, poolclass=NullPool)
        _insert_application(engine)
        engine.dispose()

        with pytest.raises(RuntimeError, match="no candidate profile"):
            command.upgrade(config, REVISION_AFTER)
    finally:
        _drop_database(database_url, target)


def test_upgrade_is_a_no_op_without_applications(database_url: str) -> None:
    target, url = _create_database(database_url, "app_empty")
    try:
        config = build_alembic_config(url)
        command.upgrade(config, REVISION_AFTER)

        engine = create_engine(url, poolclass=NullPool)
        try:
            columns = {
                c["name"]: c for c in inspect(engine).get_columns("applications")
            }
            assert columns["candidate_id"]["nullable"] is False
        finally:
            engine.dispose()
    finally:
        _drop_database(database_url, target)


def test_downgrade_removes_the_owner_column(database_url: str) -> None:
    target, url = _create_database(database_url, "app_downgrade")
    try:
        config = build_alembic_config(url)
        command.upgrade(config, REVISION_AFTER)
        command.downgrade(config, REVISION_BEFORE)

        engine = create_engine(url, poolclass=NullPool)
        try:
            columns = {
                c["name"] for c in inspect(engine).get_columns("applications")
            }
            assert "candidate_id" not in columns
        finally:
            engine.dispose()
    finally:
        _drop_database(database_url, target)
