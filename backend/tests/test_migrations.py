"""The migration history must reproduce the models exactly.

These tests run Alembic into a throwaway database rather than relying on
``alembic check``, because autogenerate does not reliably detect missing CHECK
constraints: a constraint omitted from a migration can leave the models and the
database silently disagree while ``alembic check`` still reports no drift.
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  imported for its metadata side effects
from app.db.base import Base

BACKEND_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    "agent_runs",
    "applications",
    "candidates",
    "companies",
    "documents",
    "job_analyses",
    "jobs",
    "matches",
    "sessions",
    "users",
}

EXPECTED_CHECK_CONSTRAINTS = {
    "ck_agent_runs_status_vocabulary",
    "ck_applications_status_vocabulary",
    "ck_documents_kind_vocabulary",
    "ck_documents_tailored_resume_requires_source",
    "ck_job_analyses_status_vocabulary",
    "ck_jobs_availability_vocabulary",
    "ck_jobs_crawl_status_vocabulary",
    "ck_matches_score_range",
}

IGNORED_DIFF_KEYS = {"check_constraint"}


def build_alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT))
    config.cmd_opts = Namespace(x=[f"database_url={url}"])
    return config


@pytest.fixture(scope="module")
def migrated_database_url(database_url: str) -> Iterator[str]:
    parts = urlsplit(database_url)
    base_name = parts.path.lstrip("/") or "career_copilot"
    target = f"{base_name}_migrated"
    target_url = urlunsplit(parts._replace(path=f"/{target}"))

    admin_url = urlunsplit(parts._replace(path="/postgres"))
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{target}"'))
    finally:
        admin_engine.dispose()

    yield target_url


@pytest.fixture(scope="module")
def migrated_engine(migrated_database_url: str) -> Iterator[Engine]:
    command.upgrade(build_alembic_config(migrated_database_url), "head")
    engine = create_engine(migrated_database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


def test_migrations_create_every_expected_table(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    assert EXPECTED_TABLES <= set(inspector.get_table_names())


def test_migrated_database_has_no_salary_columns(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    assert "salary_min_ctc" not in job_columns
    assert "salary_max_ctc" not in job_columns
    assert "salary_text" not in job_columns


def test_migrated_job_description_is_nullable(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    columns = {c["name"]: c for c in inspector.get_columns("jobs")}
    assert columns["description"]["nullable"] is True
    assert columns["canonical_url"]["nullable"] is False


def test_migrated_database_has_every_check_constraint(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE contype = 'c' AND connamespace = 'public'::regnamespace"
            )
        ).scalars().all()
    assert EXPECTED_CHECK_CONSTRAINTS <= set(rows)


def test_migrated_schema_matches_models(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diffs = compare_metadata(context, Base.metadata)

    unexpected = [
        diff
        for diff in diffs
        if not any(key in IGNORED_DIFF_KEYS for key in diff)
    ]
    assert unexpected == [], f"schema drift detected: {unexpected}"


def test_migrated_partial_unique_indexes_exist(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    agent_run_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("agent_runs")
    }
    analysis_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("job_analyses")
    }

    assert "uq_agent_runs_idempotency_succeeded" in agent_run_indexes
    assert "uq_job_analyses_job_prompt_succeeded" in analysis_indexes


def test_migrations_round_trip_down_and_back_up(migrated_database_url: str) -> None:
    config = build_alembic_config(migrated_database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = create_engine(migrated_database_url, poolclass=NullPool)
    try:
        inspector = inspect(engine)
        assert EXPECTED_TABLES <= set(inspector.get_table_names())
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def seeded_database_url(database_url: str) -> Iterator[str]:
    """A database left at revision 0003, holding pre-milestone rows.

    The seed rows are written at revision 0001, then the database is advanced
    to 0003 (still before an application had an owner) and one user/candidate
    pair is added. The test then upgrades to head, so the 0005 backfill has a
    single unambiguous candidate to assign the seeded application to.
    """
    parts = urlsplit(database_url)
    base_name = parts.path.lstrip("/") or "career_copilot"
    target = f"{base_name}_seeded"
    target_url = urlunsplit(parts._replace(path=f"/{target}"))

    admin_url = urlunsplit(parts._replace(path="/postgres"))
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{target}"'))
    finally:
        admin_engine.dispose()

    config = build_alembic_config(target_url)
    command.upgrade(config, "20260727_0001")

    engine = create_engine(target_url, poolclass=NullPool)
    company_id = uuid4()
    job_id = uuid4()
    application_id = uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO companies (id, name, linkedin_url, created_at, updated_at) "
                    "VALUES (:id, 'Acme Inc.', 'https://linkedin.com/company/acme', now(), now())"
                ),
                {"id": company_id},
            )
            connection.execute(
                text(
                    "INSERT INTO jobs (id, company_id, title, description, salary_min_ctc, "
                    "salary_max_ctc, salary_text, job_url, source, is_active, created_at, updated_at) "
                    "VALUES (:id, :company_id, 'AI Engineer', 'Build agents', 10, 20, '10-20 LPA', "
                    "'https://example.com/jobs/seed?utm_source=feed', 'searxng', true, now(), now())"
                ),
                {"id": job_id, "company_id": company_id},
            )
            connection.execute(
                text(
                    "INSERT INTO applications (id, job_id, status, resume_version, cover_letter, "
                    "last_updated) "
                    "VALUES (:id, :job_id, 'applied', 'v1', 'seed letter', now())"
                ),
                {"id": application_id, "job_id": job_id},
            )
    finally:
        engine.dispose()

    command.upgrade(config, "20261012_0003")
    engine = create_engine(target_url, poolclass=NullPool)
    user_id = uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, is_active, created_at, updated_at) "
                    "VALUES (:id, 'seed-owner@example.com', 'hash', true, now(), now())"
                ),
                {"id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO candidates (id, name, user_id, created_at, updated_at) "
                    "VALUES (:id, 'Seed Owner', :user_id, now(), now())"
                ),
                {"id": uuid4(), "user_id": user_id},
            )
    finally:
        engine.dispose()

    yield target_url

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{target}" WITH (FORCE)'))
    finally:
        admin_engine.dispose()


def test_upgrade_from_previous_revision_backfills_existing_rows(seeded_database_url: str) -> None:
    """Rows written before this milestone must survive the upgrade dedupable."""
    config = build_alembic_config(seeded_database_url)
    command.upgrade(config, "head")

    engine = create_engine(seeded_database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            company = connection.execute(
                text("SELECT name, normalized_name FROM companies")
            ).one()
            assert company.normalized_name == "acme"

            job = connection.execute(
                text(
                    "SELECT job_url, canonical_url, crawl_status, description, first_seen_at, "
                    "last_seen_at, availability, closed_at FROM jobs"
                )
            ).one()
            assert job.job_url == "https://example.com/jobs/seed?utm_source=feed"
            assert job.canonical_url == "https://example.com/jobs/seed"
            assert job.crawl_status == "pending"
            assert job.description == "Build agents"
            assert job.first_seen_at is not None
            assert job.last_seen_at is not None
            assert job.availability == "unknown"
            assert job.closed_at is None

            columns = {c["name"] for c in inspect(engine).get_columns("jobs")}
            assert "salary_min_ctc" not in columns
            assert "salary_text" not in columns
            assert "linkedin_url" not in {c["name"] for c in inspect(engine).get_columns("companies")}

            application = connection.execute(
                text(
                    "SELECT job_id, status, job_title_snapshot, created_at, candidate_id "
                    "FROM applications"
                )
            ).one()
            assert application.job_id is not None
            assert application.status == "applied"
            assert application.job_title_snapshot is None
            assert application.created_at is not None
            owner = connection.execute(
                text("SELECT id FROM candidates")
            ).scalar_one()
            assert application.candidate_id == owner
    finally:
        engine.dispose()