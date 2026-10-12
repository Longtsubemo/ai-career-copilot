"""Concurrent application tracking collapses to a single row.

``track_application`` guards its find-or-create with a transaction-scoped
advisory lock, so two racing "track this job" requests must still yield one
record and answer with the same id. The behaviour only exists across separate
PostgreSQL transactions, so the test drives two real connections.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.candidate import Candidate
from app.models.company import Company
from app.models.job import Job
from app.models.user import User
from app.services.applications import track_application
from tests.factories import make_candidate, make_company, make_job


def _seed_world(engine: Engine) -> tuple[UUID, UUID, UUID, UUID]:
    """Commit a candidate and a job so both workers can see them."""
    session = Session(engine)
    try:
        company = make_company(session)
        job = make_job(session, company)
        candidate = make_candidate(session)
        candidate_id, job_id, company_id, user_id = (
            candidate.id,
            job.id,
            company.id,
            candidate.user_id,
        )
        session.commit()
        return candidate_id, job_id, company_id, user_id
    finally:
        session.close()


def _cleanup(
    engine: Engine,
    *,
    candidate_id: UUID,
    job_id: UUID,
    company_id: UUID,
    user_id: UUID,
) -> None:
    """Remove the committed rows so later tests start from a clean slate."""
    session = Session(engine)
    try:
        session.execute(
            delete(Application).where(Application.candidate_id == candidate_id)
        )
        session.execute(delete(Job).where(Job.id == job_id))
        session.execute(delete(Company).where(Company.id == company_id))
        session.execute(delete(Candidate).where(Candidate.id == candidate_id))
        session.execute(delete(User).where(User.id == user_id))
        session.commit()
    finally:
        session.close()


def _count_applications(engine: Engine, job_id: UUID) -> int:
    session = Session(engine)
    try:
        return session.scalar(
            select(func.count())
            .select_from(Application)
            .where(Application.job_id == job_id)
        )
    finally:
        session.close()


def test_concurrent_tracking_collapses_to_one_application(engine: Engine) -> None:
    candidate_id, job_id, company_id, user_id = _seed_world(engine)
    first_committed = Event()
    second_attempted = Event()
    second_finished = Event()

    def track_first() -> tuple[UUID, bool]:
        session = Session(engine)
        try:
            application, created = track_application(
                session, candidate_id=candidate_id, job_id=job_id
            )
            first_committed.set()
            assert second_attempted.wait(timeout=10)
            blocked = not second_finished.wait(timeout=0.5)
            session.commit()
            assert blocked, (
                "the second worker must block on the tracking advisory lock"
            )
            return application.id, created
        finally:
            session.close()

    def track_second() -> tuple[UUID, bool]:
        assert first_committed.wait(timeout=10)
        session = Session(engine)
        try:
            second_attempted.set()
            application, created = track_application(
                session, candidate_id=candidate_id, job_id=job_id
            )
            second_finished.set()
            session.commit()
            return application.id, created
        finally:
            session.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(track_first)
            second_future = executor.submit(track_second)
            first_id, first_created = first_future.result(timeout=30)
            second_id, second_created = second_future.result(timeout=30)

        assert first_id == second_id
        assert first_created is True
        assert second_created is False
        assert _count_applications(engine, job_id) == 1
    finally:
        _cleanup(
            engine,
            candidate_id=candidate_id,
            job_id=job_id,
            company_id=company_id,
            user_id=user_id,
        )
