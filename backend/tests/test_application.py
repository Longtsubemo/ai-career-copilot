"""Application tracking semantics and history preservation."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.enums import ApplicationStatus
from tests.factories import (
    make_application_to_job,
    make_candidate,
    make_company,
    make_job,
)


def test_same_job_can_be_applied_to_more_than_once(session: Session) -> None:
    company = make_company(session)
    job = make_job(session, company)
    candidate = make_candidate(session)

    first = Application(
        candidate_id=candidate.id,
        job_id=job.id,
        status=ApplicationStatus.rejected,
        job_title_snapshot=job.title,
        company_name_snapshot=company.name,
    )
    second = Application(
        candidate_id=candidate.id,
        job_id=job.id,
        status=ApplicationStatus.draft,
        job_title_snapshot=job.title,
        company_name_snapshot=company.name,
    )
    session.add_all([first, second])
    session.commit()

    assert first.id != second.id
    assert session.query(Application).filter(Application.job_id == job.id).count() == 2


def test_application_survives_deletion_of_its_job(session: Session) -> None:
    company = make_company(session)
    application = make_application_to_job(
        session,
        company,
        title_snapshot="AI Engineer",
        company_name_snapshot="Acme Inc.",
        status=ApplicationStatus.interview,
    )
    session.commit()
    application_id = application.id
    job_id = application.job_id

    session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
    session.commit()
    session.expire_all()

    surviving = session.get(Application, application_id)
    assert surviving is not None, "deleting a job must not delete an application"
    assert surviving.job_id is None
    assert surviving.status == ApplicationStatus.interview
    assert surviving.job_title_snapshot == "AI Engineer"
    assert surviving.company_name_snapshot == "Acme Inc."


def test_status_defaults_to_draft(session: Session) -> None:
    company = make_company(session)
    application = make_application_to_job(session, company)
    session.commit()
    session.expire_all()

    reloaded = session.get(Application, application.id)
    assert reloaded is not None
    assert reloaded.status == ApplicationStatus.draft


def test_status_vocabulary_is_enforced_by_the_database(session: Session) -> None:
    company = make_company(session)
    application = make_application_to_job(session, company)
    session.commit()

    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE applications SET status = 'ghosting' WHERE id = :id"),
            {"id": application.id},
        )
        session.commit()
    session.rollback()


@pytest.mark.parametrize(
    "status",
    [
        ApplicationStatus.draft,
        ApplicationStatus.applied,
        ApplicationStatus.interview,
        ApplicationStatus.offer,
        ApplicationStatus.rejected,
        ApplicationStatus.withdrawn,
    ],
)
def test_every_declared_status_is_accepted(session: Session, status: ApplicationStatus) -> None:
    company = make_company(session)
    application = make_application_to_job(session, company, status=status)
    session.commit()

    session.expire_all()
    reloaded = session.get(Application, application.id)
    assert reloaded is not None
    assert reloaded.status == status


def test_application_carries_timestamp_columns(session: Session) -> None:
    company = make_company(session)
    application = make_application_to_job(session, company)
    session.commit()
    session.expire_all()

    reloaded = session.get(Application, application.id)
    assert reloaded is not None
    assert reloaded.created_at is not None
    assert reloaded.updated_at is not None


def test_applied_at_is_independent_of_created_at(session: Session) -> None:
    from datetime import datetime, timezone

    company = make_company(session)
    applied_at = datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc)
    application = make_application_to_job(
        session,
        company,
        status=ApplicationStatus.applied,
        applied_at=applied_at,
    )
    session.commit()

    assert application.applied_at == applied_at


def test_job_relationship_exposes_many_applications(session: Session) -> None:
    company = make_company(session)
    job = make_job(session, company)
    candidate = make_candidate(session)
    session.add_all(
        [
            Application(
                candidate_id=candidate.id,
                job_id=job.id,
                job_title_snapshot=job.title,
            ),
            Application(
                candidate_id=candidate.id,
                job_id=job.id,
                job_title_snapshot=job.title,
            ),
        ]
    )
    session.commit()
    session.expire_all()

    reloaded = session.get(type(job), job.id)
    assert reloaded is not None
    assert len(reloaded.applications) == 2
    assert all(application.job_id == job.id for application in reloaded.applications)