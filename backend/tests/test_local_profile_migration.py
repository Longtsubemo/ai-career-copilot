"""Fresh and existing PostgreSQL databases migrate without losing app data."""

from __future__ import annotations

from argparse import Namespace
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.candidate import Candidate
from app.models.document import Document
from app.models.discovery_daily_usage import DiscoveryDailyUsage
from app.models.enums import ApplicationStatus, DocumentKind, RunStatus
from app.models.job_analysis import JobAnalysis
from app.models.match import Match
from app.services.candidates import (
    LocalProfileSelectionRequired,
    get_local_candidate,
    select_local_candidate,
)
from tests.factories import make_company, make_job

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PRE_LOCAL_PROFILE_REVISION = "20261016_0008"


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT))
    config.cmd_opts = Namespace(x=[f"database_url={url}"])
    return config


def _new_database(database_url: str) -> tuple[str, str]:
    parts = urlsplit(database_url)
    name = f"{parts.path.lstrip('/')}_local_{uuid4().hex[:10]}"
    url = urlunsplit(parts._replace(path=f"/{name}"))
    admin_url = urlunsplit(parts._replace(path="/postgres"))
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()
    return name, url


def _drop_database(database_url: str, name: str) -> None:
    admin_url = urlunsplit(urlsplit(database_url)._replace(path="/postgres"))
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        engine.dispose()


def _insert_account_and_candidate(
    engine, *, name: str, email: str
) -> tuple[object, object]:
    user_id, candidate_id = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, password_hash, is_active, created_at, updated_at) "
                "VALUES (:id, :email, 'synthetic-hash', true, now(), now())"
            ),
            {"id": user_id, "email": email},
        )
        connection.execute(
            text(
                "INSERT INTO candidates (id, name, user_id, created_at, updated_at) "
                "VALUES (:id, :name, :user_id, now(), now())"
            ),
            {"id": candidate_id, "name": name, "user_id": user_id},
        )
    return user_id, candidate_id


def test_existing_single_profile_upgrade_preserves_related_records(database_url: str):
    name, url = _new_database(database_url)
    engine = create_engine(url, poolclass=NullPool)
    try:
        config = _alembic_config(url)
        command.upgrade(config, PRE_LOCAL_PROFILE_REVISION)
        user_id, candidate_id = _insert_account_and_candidate(
            engine, name="Ada Lovelace", email="ada@example.invalid"
        )

        with Session(engine) as session:
            company = make_company(session, name="Synthetic Co")
            job = make_job(session, company, title="AI Engineer")
            application = Application(
                candidate_id=candidate_id,
                job_id=job.id,
                status=ApplicationStatus.applied,
                notes="synthetic application",
            )
            session.add(application)
            session.flush()
            resume = Document(
                candidate_id=candidate_id,
                kind=DocumentKind.master_resume,
                content="synthetic resume evidence",
            )
            analysis = JobAnalysis(
                job_id=job.id,
                prompt_version="test-v1",
                status=RunStatus.succeeded,
                analysis={"summary": "synthetic analysis"},
            )
            session.add_all((resume, analysis))
            session.flush()
            match = Match(
                job_id=job.id,
                candidate_id=candidate_id,
                resume_document_id=resume.id,
                job_analysis_id=analysis.id,
                score=80,
                verdict={"rationale": "synthetic match"},
                prompt_version="test-v1",
            )
            run = AgentRun(
                agent_name="job_analysis",
                status=RunStatus.succeeded,
                idempotency_key="migration-test-run",
                input={"job_id": str(job.id)},
                output={"summary": "synthetic analysis"},
                job_id=job.id,
                candidate_id=candidate_id,
            )
            session.add_all((match, run))
            session.add_all(
                (
                    DiscoveryDailyUsage(
                        usage_date=date(2026, 11, 14),
                        scope_key=f"user:{user_id}",
                        attempts=2,
                    ),
                    DiscoveryDailyUsage(
                        usage_date=date(2026, 11, 14),
                        scope_key="global",
                        attempts=3,
                    ),
                )
            )
            session.commit()
            saved_ids = {
                "application": application.id,
                "resume": resume.id,
                "analysis": analysis.id,
                "match": match.id,
                "run": run.id,
                "job": job.id,
            }

        command.upgrade(config, "head")
        command.upgrade(config, "head")
        command.check(config)

        with Session(engine) as session:
            candidate = session.get(Candidate, candidate_id)
            assert candidate is not None
            assert candidate.name == "Ada Lovelace"
            assert candidate.is_local_profile is True
            assert session.get(Application, saved_ids["application"]).candidate_id == candidate_id
            assert session.get(Document, saved_ids["resume"]).candidate_id == candidate_id
            assert session.get(JobAnalysis, saved_ids["analysis"]).job_id == saved_ids["job"]
            assert session.get(Match, saved_ids["match"]).candidate_id == candidate_id
            assert session.get(AgentRun, saved_ids["run"]).candidate_id == candidate_id
            assert session.get(DiscoveryDailyUsage, (date(2026, 11, 14), "local")).attempts == 2
            assert session.get(DiscoveryDailyUsage, (date(2026, 11, 14), "global")).attempts == 3
            assert get_local_candidate(session)[0].id == candidate_id

        inspector = inspect(engine)
        assert not {"users", "sessions"} & set(inspector.get_table_names())
        columns = {column["name"] for column in inspector.get_columns("candidates")}
        assert "user_id" not in columns
    finally:
        engine.dispose()
        _drop_database(database_url, name)


def test_multiple_profiles_are_preserved_until_explicitly_selected(database_url: str):
    name, url = _new_database(database_url)
    engine = create_engine(url, poolclass=NullPool)
    try:
        config = _alembic_config(url)
        command.upgrade(config, PRE_LOCAL_PROFILE_REVISION)
        _user1, first_id = _insert_account_and_candidate(
            engine, name="Ada Lovelace", email="ada@example.invalid"
        )
        _user2, second_id = _insert_account_and_candidate(
            engine, name="Grace Hopper", email="grace@example.invalid"
        )
        with Session(engine) as session:
            for candidate_id, name_label in (
                (first_id, "Ada Lovelace"),
                (second_id, "Grace Hopper"),
            ):
                session.add(
                    Document(
                        candidate_id=candidate_id,
                        kind=DocumentKind.master_resume,
                        content=f"synthetic resume for {name_label}",
                    )
                )
                session.add(
                    Application(
                        candidate_id=candidate_id,
                        status=ApplicationStatus.draft,
                        job_title_snapshot=f"Role for {name_label}",
                        company_name_snapshot="Synthetic Co",
                    )
                )
            session.commit()

        command.upgrade(config, "head")
        command.check(config)

        with Session(engine) as session:
            profiles = session.execute(
                select(Candidate).order_by(Candidate.name)
            ).scalars().all()
            assert {profile.id for profile in profiles} == {first_id, second_id}
            assert {profile.name for profile in profiles} == {"Ada Lovelace", "Grace Hopper"}
            assert all(not profile.is_local_profile for profile in profiles)
            assert session.scalar(
                select(func.count()).select_from(Document)
            ) == 2
            assert session.scalar(
                select(func.count()).select_from(Application)
            ) == 2
            with pytest.raises(LocalProfileSelectionRequired):
                get_local_candidate(session)

            selected = select_local_candidate(session, second_id)
            session.commit()
            assert selected.id == second_id
            # Re-selecting the current profile must keep it selected.
            select_local_candidate(session, second_id)
            session.commit()

        with Session(engine) as session:
            current, changed = get_local_candidate(session)
            assert changed is False
            assert current.id == second_id
            assert session.get(Candidate, first_id).name == "Ada Lovelace"
    finally:
        engine.dispose()
        _drop_database(database_url, name)
