"""The candidate-owner migration (0004) must guard and enforce ownership.

Revision 0003 left ``candidates.user_id`` nullable so a legacy, ownerless
profile could survive until an operator bound it. Revision 0004 contracts the
column to NOT NULL. These tests prove the guard: an upgrade with an ownerless
row aborts with an actionable error instead of guessing, an upgrade with every
row owned succeeds, and a downgrade restores the relaxed column.
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

REVISION_WITH_NULLABLE_OWNER = "20261012_0003"
REVISION_WITH_REQUIRED_OWNER = "20261012_0004"


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


def _insert_user(engine: Engine, email: str) -> str:
    user_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_active, created_at, updated_at) "
                "VALUES (:id, :email, 'hash', true, now(), now())"
            ),
            {"id": user_id, "email": email},
        )
    return user_id


def test_upgrade_aborts_when_a_candidate_has_no_owner(database_url: str) -> None:
    target, url = _create_database(database_url, "ownerless")
    try:
        config = build_alembic_config(url)
        command.upgrade(config, REVISION_WITH_NULLABLE_OWNER)

        engine = create_engine(url, poolclass=NullPool)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO candidates (id, name, user_id, created_at, updated_at) "
                    "VALUES (:id, 'Legacy', NULL, now(), now())"
                ),
                {"id": str(uuid4())},
            )
        engine.dispose()

        with pytest.raises(RuntimeError, match="no owner"):
            command.upgrade(config, REVISION_WITH_REQUIRED_OWNER)
    finally:
        _drop_database(database_url, target)


def test_upgrade_succeeds_when_every_candidate_is_owned(database_url: str) -> None:
    target, url = _create_database(database_url, "owned")
    try:
        config = build_alembic_config(url)
        command.upgrade(config, REVISION_WITH_NULLABLE_OWNER)

        engine = create_engine(url, poolclass=NullPool)
        user_id = _insert_user(engine, "owner@example.com")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO candidates (id, name, user_id, created_at, updated_at) "
                    "VALUES (:id, 'Owned', :user_id, now(), now())"
                ),
                {"id": str(uuid4()), "user_id": user_id},
            )
        engine.dispose()

        command.upgrade(config, REVISION_WITH_REQUIRED_OWNER)

        engine = create_engine(url, poolclass=NullPool)
        try:
            columns = {c["name"]: c for c in inspect(engine).get_columns("candidates")}
            assert columns["user_id"]["nullable"] is False
        finally:
            engine.dispose()
    finally:
        _drop_database(database_url, target)


def test_downgrade_relaxes_the_owner_again(database_url: str) -> None:
    target, url = _create_database(database_url, "relaxed")
    try:
        config = build_alembic_config(url)
        command.upgrade(config, REVISION_WITH_REQUIRED_OWNER)
        command.downgrade(config, REVISION_WITH_NULLABLE_OWNER)

        engine = create_engine(url, poolclass=NullPool)
        try:
            columns = {c["name"]: c for c in inspect(engine).get_columns("candidates")}
            assert columns["user_id"]["nullable"] is True
        finally:
            engine.dispose()
    finally:
        _drop_database(database_url, target)
