"""Versioned, idempotent job analysis storage."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import RunStatus
from app.models.job import Job
from app.models.job_analysis import JobAnalysis
from tests.factories import make_job


def make_analysis(session: Session, job_id, prompt_version: str, status: RunStatus, **overrides):
    analysis = JobAnalysis(
        job_id=job_id,
        prompt_version=prompt_version,
        status=status,
        **overrides,
    )
    session.add(analysis)
    session.flush()
    return analysis


def test_analysis_defaults_to_pending(session: Session) -> None:
    job = make_job(session)
    analysis = make_analysis(session, job.id, "v1", RunStatus.pending)
    session.commit()

    assert analysis.status == RunStatus.pending
    assert analysis.analysis is None
    assert analysis.created_at is not None


def test_structured_analysis_round_trips_through_jsonb(session: Session) -> None:
    job = make_job(session)
    payload = {
        "required_skills": ["python", "fastapi", "postgres"],
        "seniority": "senior",
        "responsibilities": ["build agents"],
        "remote": True,
    }
    analysis = make_analysis(
        session,
        job.id,
        "v1",
        RunStatus.succeeded,
        analysis=payload,
        model="gemini-2.5-pro",
    )
    session.commit()
    session.expire_all()

    reloaded = session.get(JobAnalysis, analysis.id)
    assert reloaded is not None
    assert reloaded.analysis == payload
    assert reloaded.model == "gemini-2.5-pro"


def test_same_prompt_version_cannot_succeed_twice_for_one_job(session: Session) -> None:
    job = make_job(session)
    make_analysis(session, job.id, "v1", RunStatus.succeeded)
    session.commit()

    with pytest.raises(IntegrityError):
        make_analysis(session, job.id, "v1", RunStatus.succeeded)
        session.commit()
    session.rollback()


def test_different_prompt_versions_coexist(session: Session) -> None:
    job = make_job(session)
    first = make_analysis(session, job.id, "v1", RunStatus.succeeded, analysis={"rev": 1})
    second = make_analysis(session, job.id, "v2", RunStatus.succeeded, analysis={"rev": 2})
    session.commit()

    assert first.id != second.id


def test_failed_analysis_may_be_retried_for_the_same_prompt_version(session: Session) -> None:
    job = make_job(session)
    make_analysis(session, job.id, "v1", RunStatus.failed, analysis=None)
    session.commit()

    make_analysis(session, job.id, "v1", RunStatus.succeeded, analysis={"rev": 1})
    session.commit()


def test_same_prompt_version_may_succeed_for_different_jobs(session: Session) -> None:
    first_job = make_job(session)
    second_job = make_job(session)
    make_analysis(session, first_job.id, "v1", RunStatus.succeeded)
    make_analysis(session, second_job.id, "v1", RunStatus.succeeded)
    session.commit()


def test_prompt_version_is_required(session: Session) -> None:
    job = make_job(session)
    analysis = JobAnalysis(job_id=job.id, status=RunStatus.succeeded)
    session.add(analysis)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_analysis_is_removed_with_its_job(session: Session) -> None:
    job = make_job(session)
    make_analysis(session, job.id, "v1", RunStatus.succeeded, analysis={"rev": 1})
    session.commit()
    job_id = job.id

    session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
    session.commit()

    assert session.query(JobAnalysis).filter(JobAnalysis.job_id == job_id).count() == 0
    assert session.get(Job, job_id) is None


def test_job_relationship_exposes_versioned_analyses(session: Session) -> None:
    job = make_job(session)
    make_analysis(session, job.id, "v1", RunStatus.succeeded, analysis={"rev": 1})
    make_analysis(session, job.id, "v2", RunStatus.succeeded, analysis={"rev": 2})
    session.commit()
    session.expire_all()

    reloaded = session.get(Job, job.id)
    assert reloaded is not None
    assert len(reloaded.analyses) == 2
    assert {analysis.prompt_version for analysis in reloaded.analyses} == {"v1", "v2"}