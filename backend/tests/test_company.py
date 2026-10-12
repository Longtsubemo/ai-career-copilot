"""Company dedupe and the guarantee that cleanup cannot erase applications."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.enums import ApplicationStatus
from app.models.job import Job
from app.services.normalization import normalize_company_name
from tests.factories import make_application_to_job, make_company, make_job


def test_duplicate_normalized_name_is_rejected(session: Session) -> None:
    make_company(session, name="Acme Inc.")
    session.commit()

    with pytest.raises(IntegrityError):
        make_company(session, name="ACME, INC.", normalized_name="acme")
        session.commit()
    session.rollback()


def test_same_normalized_name_from_variants_conflicts(session: Session) -> None:
    make_company(session, name="Globex Ltd")
    session.commit()

    assert normalize_company_name("Globex Limited") == "globex"

    with pytest.raises(IntegrityError):
        make_company(session, name="Globex Limited")
        session.commit()
    session.rollback()


def test_distinct_companies_are_allowed(session: Session) -> None:
    make_company(session, name="Acme Inc.")
    make_company(session, name="Globex Ltd")
    session.commit()


def test_deleting_company_does_not_delete_its_jobs(session: Session) -> None:
    company = make_company(session, name="Acme Inc.")
    job = make_job(session, company)
    session.commit()
    job_id = job.id

    session.delete(company)
    session.commit()
    session.expire_all()

    surviving = session.get(Job, job_id)
    assert surviving is not None, "job must survive deletion of its company"
    assert surviving.company_id is None


def test_deleting_company_does_not_destroy_application_history(session: Session) -> None:
    company = make_company(session, name="Acme Inc.")
    application = make_application_to_job(
        session,
        company,
        title_snapshot="AI Engineer",
        company_name_snapshot="Acme Inc.",
        status=ApplicationStatus.applied,
    )
    session.commit()
    application_id = application.id
    job_id = application.job_id

    session.delete(company)
    session.commit()
    session.expire_all()

    surviving = session.get(Application, application_id)
    assert surviving is not None, "an application must never be cascade deleted"
    assert surviving.job_id == job_id, "the job survives, so the link stays intact"
    assert surviving.job_title_snapshot == "AI Engineer"
    assert surviving.company_name_snapshot == "Acme Inc."


def test_application_survives_the_whole_company_job_chain(session: Session) -> None:
    company = make_company(session, name="Acme Inc.")
    application = make_application_to_job(
        session,
        company,
        title_snapshot="AI Engineer",
        company_name_snapshot="Acme Inc.",
    )
    session.commit()
    application_id = application.id

    session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": application.job_id})
    session.commit()
    session.expire_all()

    surviving = session.get(Application, application_id)
    assert surviving is not None
    assert surviving.job_id is None
    assert surviving.company_name_snapshot == "Acme Inc."