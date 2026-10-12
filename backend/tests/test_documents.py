"""Document versioning: generated artifacts are appended, never overwritten."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.enums import DocumentKind
from tests.factories import (
    make_application_to_job,
    make_candidate,
    make_company,
    make_document,
    make_job,
    make_master_resume,
)


def test_master_resume_needs_no_job(session: Session) -> None:
    candidate = make_candidate(session)
    resume = make_master_resume(session, candidate)
    session.commit()

    assert resume.kind == DocumentKind.master_resume
    assert resume.job_id is None
    assert resume.source_document_id is None


def test_all_three_document_kinds_are_accepted(session: Session) -> None:
    candidate = make_candidate(session)
    master = make_master_resume(session, candidate)

    make_document(
        session,
        candidate,
        DocumentKind.tailored_resume,
        "Tailored for the role",
        source_document_id=master.id,
    )
    make_document(session, candidate, DocumentKind.cover_letter, "Dear hiring manager")
    session.commit()

    kinds = set(
        session.query(Document.kind).filter(Document.candidate_id == candidate.id).all()
    )
    assert {row[0] for row in kinds} == set(DocumentKind)


def test_tailored_resume_requires_a_source_document(session: Session) -> None:
    candidate = make_candidate(session)
    with pytest.raises(IntegrityError):
        make_document(
            session,
            candidate,
            DocumentKind.tailored_resume,
            "Tailored with no origin",
            source_document_id=None,
        )
        session.commit()
    session.rollback()


def test_invalid_document_kind_is_rejected(session: Session) -> None:
    candidate = make_candidate(session)
    document = make_master_resume(session, candidate)
    session.commit()

    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE documents SET kind = 'cover_leter' WHERE id = :id"),
            {"id": document.id},
        )
        session.commit()
    session.rollback()


def test_regenerated_tailored_resumes_are_kept_as_separate_versions(session: Session) -> None:
    candidate = make_candidate(session)
    master = make_master_resume(session, candidate)

    first = make_document(
        session,
        candidate,
        DocumentKind.tailored_resume,
        "Version one",
        source_document_id=master.id,
    )
    second = make_document(
        session,
        candidate,
        DocumentKind.tailored_resume,
        "Version two",
        source_document_id=master.id,
    )
    session.commit()

    assert first.id != second.id
    assert first.content == "Version one"
    assert second.content == "Version two"

    session.expire_all()
    reloaded = session.get(Document, first.id)
    assert reloaded is not None
    assert reloaded.content == "Version one", "earlier versions must survive"


def test_source_document_relationship_lists_derived_documents(session: Session) -> None:
    candidate = make_candidate(session)
    master = make_master_resume(session, candidate)
    make_document(
        session,
        candidate,
        DocumentKind.tailored_resume,
        "Tailored A",
        source_document_id=master.id,
    )
    make_document(
        session,
        candidate,
        DocumentKind.tailored_resume,
        "Tailored B",
        source_document_id=master.id,
    )
    session.commit()
    session.expire_all()

    reloaded_master = session.get(Document, master.id)
    assert reloaded_master is not None
    derived = reloaded_master.derived_documents
    assert len(derived) == 2
    assert {document.source_document_id for document in derived} == {master.id}


def test_document_provenance_is_recorded(session: Session) -> None:
    candidate = make_candidate(session)
    document = make_master_resume(
        session,
        candidate,
        model="gemini-2.5-pro",
        prompt_version="v3",
    )
    session.commit()

    assert document.model == "gemini-2.5-pro"
    assert document.prompt_version == "v3"


def test_document_survives_deletion_of_its_job(session: Session) -> None:
    candidate = make_candidate(session)
    company = make_company(session)
    job = make_job(session, company)
    letter = make_document(
        session,
        candidate,
        DocumentKind.cover_letter,
        "Dear hiring manager",
        job_id=job.id,
    )
    session.commit()
    letter_id = letter.id

    session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job.id})
    session.commit()
    session.expire_all()

    surviving = session.get(Document, letter_id)
    assert surviving is not None
    assert surviving.job_id is None
    assert surviving.content == "Dear hiring manager"


def test_cover_letter_can_be_linked_to_an_application(session: Session) -> None:
    candidate = make_candidate(session)
    company = make_company(session)
    application = make_application_to_job(session, company)
    letter = make_document(
        session,
        candidate,
        DocumentKind.cover_letter,
        "Dear hiring manager",
        application_id=application.id,
    )
    session.commit()
    session.expire_all()

    reloaded = session.get(Document, letter.id)
    assert reloaded is not None
    assert reloaded.application is not None
    assert reloaded.application.id == application.id


def test_deleting_application_detaches_document(session: Session) -> None:
    candidate = make_candidate(session)
    company = make_company(session)
    application = make_application_to_job(session, company)
    letter = make_document(
        session,
        candidate,
        DocumentKind.cover_letter,
        "Dear hiring manager",
        application_id=application.id,
    )
    session.commit()
    letter_id = letter.id

    session.execute(
        text("DELETE FROM applications WHERE id = :id"), {"id": application.id}
    )
    session.commit()
    session.expire_all()

    surviving = session.get(Document, letter_id)
    assert surviving is not None
    assert surviving.application_id is None


def test_document_records_json_free_text_content(session: Session) -> None:
    candidate = make_candidate(session)
    long_content = "line\n" * 500
    document = make_master_resume(session, candidate, content=long_content)
    session.commit()
    session.expire_all()

    reloaded = session.get(Document, document.id)
    assert reloaded is not None
    assert reloaded.content == long_content