"""Deterministic master resume upload behaviour.

Uploading a resume is application functionality — no Gemini, no agent run.
These tests exercise the service against PostgreSQL through the savepoint
``session`` fixture and prove that an uploaded master resume is consumable by
the existing Job Matching agent using the fake Gemini client.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.agents.job_matching import (
    JobMatchingAgent,
    MatchResult,
    MatchSignal,
    MatchVerdict,
)
from app.models.agent_run import AgentRun
from app.models.document import Document
from app.models.enums import DocumentKind, RunStatus
from app.models.job_analysis import JobAnalysis
from app.models.match import Match
from app.services.resume_upload import (
    CandidateNotFoundError,
    EmptyResumeError,
    EncryptedResumeError,
    ImageOnlyResumeError,
    InvalidResumeError,
    OversizedResumeError,
    ResumeUploadResult,
    UnsupportedResumeFormatError,
    current_master_resume,
    extract_resume_text,
    master_resume_history,
    upload_master_resume,
)
from tests.factories import make_candidate, make_company, make_job, make_master_resume
from tests.fake_clients import FakeGeminiClient
from tests.resume_fixtures import (
    make_docx_bytes,
    make_encrypted_pdf_bytes,
    make_pdf_bytes,
)

_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

MATCH = MatchResult(
    score=70.0,
    verdict=MatchVerdict.good_match,
    matched_skills=["python", "fastapi"],
    strengths=["backend agent pipelines"],
    experience_match=MatchSignal.matched,
    education_match=MatchSignal.unclear,
    location_match=MatchSignal.matched,
    seniority_match=MatchSignal.matched,
    rationale="Strong backend overlap with the role.",
    confidence=0.8,
)

ANALYSIS_PAYLOAD = {
    "summary": "Backend role building agentic systems.",
    "confidence": 0.9,
    "required_skills": ["python", "postgresql"],
    "preferred_skills": ["kubernetes"],
    "experience_years_min": 3,
    "experience_years_max": 6,
    "seniority": "mid",
}

_PDF_MAGIC = b"%PDF-1.7\nnot really a pdf"

_DOCX_MAGIC = b"PK\x03\x04 some zip bytes"


def document_count(session) -> int:
    return len(session.scalars(select(Document)).all())


def upload(session, candidate, *, content=b"name", **kwargs):
    return upload_master_resume(
        session,
        candidate_id=candidate.id,
        content=content,
        filename="resume.txt",
        **kwargs,
    )


def test_upload_requires_an_existing_candidate(session) -> None:
    with pytest.raises(CandidateNotFoundError):
        upload_master_resume(
            session,
            candidate_id=uuid4(),
            content=b"resume",
            filename="resume.txt",
        )

    assert document_count(session) == 0


def test_upload_creates_master_resume_for_the_candidate(session) -> None:
    candidate = make_candidate(session, name="Ada Lovelace")
    result = upload(session, candidate, content=b"Ada is a backend engineer.")

    assert isinstance(result, ResumeUploadResult)
    assert result.candidate_id == candidate.id
    assert result.document_id is not None
    assert result.kind == DocumentKind.master_resume
    assert result.filename == "resume.txt"
    assert result.created_at is not None

    row = session.get(Document, result.document_id)
    assert row is not None
    assert row.kind == DocumentKind.master_resume
    assert row.candidate_id == candidate.id
    assert row.content == "Ada is a backend engineer."
    assert row.source_document_id is None
    assert row.job_id is None
    assert row.application_id is None
    assert row.agent_run_id is None
    assert row.model is None
    assert row.prompt_version is None


def test_upload_accepts_content_type_with_parameters(session) -> None:
    candidate = make_candidate(session)
    result = upload(
        session,
        candidate,
        content=b"plain text resume",
        content_type="text/plain; charset=utf-8",
    )

    assert session.get(Document, result.document_id).content == "plain text resume"


def test_upload_normalizes_line_endings_and_strips_bom(session) -> None:
    candidate = make_candidate(session)
    result = upload(
        session,
        candidate,
        content=b"\xef\xbb\xbfName: Ada\r\nRole: Engineer\r\n",
    )

    row = session.get(Document, result.document_id)
    assert row is not None
    assert row.content == "Name: Ada\nRole: Engineer"


def test_upload_rejects_empty_content(session) -> None:
    candidate = make_candidate(session)
    with pytest.raises(EmptyResumeError):
        upload(session, candidate, content=b"")

    with pytest.raises(EmptyResumeError):
        upload(session, candidate, content=b"   \n\t ")

    assert document_count(session) == 0


def test_upload_rejects_unsupported_content_type(session) -> None:
    candidate = make_candidate(session)
    with pytest.raises(UnsupportedResumeFormatError):
        upload(session, candidate, content_type="application/msword", content=b"legacy")
    with pytest.raises(UnsupportedResumeFormatError):
        upload(session, candidate, content_type="application/octet-stream")

    assert document_count(session) == 0


def test_upload_rejects_binary_bytes_even_when_labeled_text(session) -> None:
    candidate = make_candidate(session)
    for binary in (_PDF_MAGIC, _DOCX_MAGIC):
        with pytest.raises(UnsupportedResumeFormatError):
            upload(session, candidate, content=binary, content_type="text/plain")

    with pytest.raises(UnsupportedResumeFormatError):
        upload(session, candidate, content=_PDF_MAGIC)

    assert document_count(session) == 0


def test_upload_rejects_non_utf8_content(session) -> None:
    candidate = make_candidate(session)
    with pytest.raises(UnsupportedResumeFormatError):
        upload(session, candidate, content=b"\xff\xfe\x00 not text")

    assert document_count(session) == 0


def test_upload_accepts_markdown(session) -> None:
    candidate = make_candidate(session)
    result = upload_master_resume(
        session,
        candidate_id=candidate.id,
        content=b"# Ada\nEngineer",
        filename="resume.md",
        content_type="text/markdown",
    )

    assert session.get(Document, result.document_id).content == "# Ada\nEngineer"


def test_extract_pdf_text_preserves_paragraph_order() -> None:
    pdf = make_pdf_bytes("First paragraph", "Second paragraph", "Third paragraph")

    text = extract_resume_text(
        pdf, content_type="application/pdf", filename="resume.pdf"
    )

    assert text.split("\n") == [
        "First paragraph",
        "Second paragraph",
        "Third paragraph",
    ]


def test_upload_extracts_text_from_a_pdf(session) -> None:
    candidate = make_candidate(session)
    pdf = make_pdf_bytes("Ada Lovelace", "Backend Engineer", "Python and FastAPI")

    result = upload_master_resume(
        session,
        candidate_id=candidate.id,
        content=pdf,
        filename="resume.pdf",
        content_type="application/pdf",
    )

    row = session.get(Document, result.document_id)
    assert row is not None
    assert row.content == "Ada Lovelace\nBackend Engineer\nPython and FastAPI"
    assert row.kind == DocumentKind.master_resume


def test_upload_extracts_docx_paragraphs_and_table(session) -> None:
    candidate = make_candidate(session)
    docx = make_docx_bytes(
        ["Ada Lovelace", "Experience"],
        [["Skill", "Years"], ["Python", "5"]],
    )

    result = upload_master_resume(
        session,
        candidate_id=candidate.id,
        content=docx,
        filename="resume.docx",
        content_type=_DOCX_MIME,
    )

    row = session.get(Document, result.document_id)
    assert row is not None
    assert row.content == "Ada Lovelace\nExperience\nSkill | Years\nPython | 5"


def test_upload_rejects_image_only_pdf_with_ocr_message(session) -> None:
    candidate = make_candidate(session)

    with pytest.raises(ImageOnlyResumeError) as excinfo:
        upload_master_resume(
            session,
            candidate_id=candidate.id,
            content=make_pdf_bytes(),
            filename="scan.pdf",
            content_type="application/pdf",
        )

    assert "ocr" in str(excinfo.value).lower()
    assert document_count(session) == 0


def test_upload_rejects_corrupted_pdf(session) -> None:
    candidate = make_candidate(session)

    with pytest.raises(UnsupportedResumeFormatError):
        upload_master_resume(
            session,
            candidate_id=candidate.id,
            content=b"%PDF-1.7 definitely not a real pdf",
            filename="broken.pdf",
            content_type="application/pdf",
        )

    assert document_count(session) == 0


def test_upload_rejects_encrypted_pdf(session) -> None:
    candidate = make_candidate(session)

    with pytest.raises(EncryptedResumeError):
        upload_master_resume(
            session,
            candidate_id=candidate.id,
            content=make_encrypted_pdf_bytes("hidden text"),
            filename="locked.pdf",
            content_type="application/pdf",
        )

    assert document_count(session) == 0


def test_upload_rejects_empty_docx(session) -> None:
    candidate = make_candidate(session)

    with pytest.raises(EmptyResumeError):
        upload_master_resume(
            session,
            candidate_id=candidate.id,
            content=make_docx_bytes([]),
            filename="empty.docx",
            content_type=_DOCX_MIME,
        )

    assert document_count(session) == 0


def test_upload_rejects_unsupported_extension(session) -> None:
    candidate = make_candidate(session)

    with pytest.raises(UnsupportedResumeFormatError):
        upload_master_resume(
            session,
            candidate_id=candidate.id,
            content=b"payload",
            filename="resume.rtf",
            content_type="text/plain",
        )

    assert document_count(session) == 0


def test_upload_rejects_content_that_mismatches_its_extension(session) -> None:
    candidate = make_candidate(session)

    with pytest.raises(UnsupportedResumeFormatError):
        upload_master_resume(
            session,
            candidate_id=candidate.id,
            content=b"plain text renamed as a pdf",
            filename="resume.pdf",
            content_type="application/pdf",
        )
    with pytest.raises(UnsupportedResumeFormatError):
        upload_master_resume(
            session,
            candidate_id=candidate.id,
            content=make_pdf_bytes("real pdf"),
            filename="resume.txt",
            content_type="text/plain",
        )

    assert document_count(session) == 0


def test_pdf_and_docx_uploads_append_versions(session) -> None:
    candidate = make_candidate(session)

    upload(session, candidate, content=b"plain version")
    upload_master_resume(
        session,
        candidate_id=candidate.id,
        content=make_pdf_bytes("PDF version"),
        filename="resume.pdf",
        content_type="application/pdf",
    )
    docx = upload_master_resume(
        session,
        candidate_id=candidate.id,
        content=make_docx_bytes(["DOCX version"]),
        filename="resume.docx",
        content_type=_DOCX_MIME,
    )

    rows = session.scalars(
        select(Document).where(Document.candidate_id == candidate.id)
    ).all()
    assert len(rows) == 3
    assert {row.kind for row in rows} == {DocumentKind.master_resume}
    assert {row.content for row in rows} == {
        "plain version",
        "PDF version",
        "DOCX version",
    }
    assert sum(1 for row in rows if row.source_document_id is None) == 1
    assert docx.document_id in {row.id for row in rows}


def test_current_master_resume_is_the_newest_version(session) -> None:
    candidate = make_candidate(session)
    older = make_master_resume(
        session,
        candidate,
        content="older",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    newer = make_master_resume(
        session,
        candidate,
        content="newer",
        created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )

    current = current_master_resume(session, candidate.id)

    assert current is not None
    assert current.id == newer.id
    history = master_resume_history(session, candidate.id)
    assert [row.id for row in history] == [newer.id, older.id]


def test_upload_rejects_oversized_content(session) -> None:
    candidate = make_candidate(session)
    with pytest.raises(OversizedResumeError):
        upload(session, candidate, content=b"x" * 512, max_bytes=128)

    assert document_count(session) == 0


def test_upload_rejects_blank_filename(session) -> None:
    candidate = make_candidate(session)
    with pytest.raises(InvalidResumeError):
        upload_master_resume(
            session,
            candidate_id=candidate.id,
            content=b"resume",
            filename="   ",
        )

    assert document_count(session) == 0


def test_second_upload_preserves_the_previous_master_resume(session) -> None:
    candidate = make_candidate(session)
    first = upload(session, candidate, content=b"first master version")
    second = upload(session, candidate, content=b"second master version")

    assert second.document_id != first.document_id
    assert second.created_at >= first.created_at

    first_row = session.get(Document, first.document_id)
    second_row = session.get(Document, second.document_id)
    assert first_row is not None and second_row is not None
    assert first_row.content == "first master version"
    assert first_row.source_document_id is None
    assert second_row.content == "second master version"
    assert second_row.source_document_id == first.document_id
    assert second_row.kind == DocumentKind.master_resume

    masters = session.scalars(
        select(Document).where(Document.candidate_id == candidate.id)
    ).all()
    assert len(masters) == 2


def test_upload_does_not_create_an_agent_run(session) -> None:
    candidate = make_candidate(session)
    upload(session, candidate)

    assert (
        session.scalar(select(func.count()).select_from(AgentRun)) == 0
    )


def test_uploaded_master_resume_is_consumable_by_job_matching(session) -> None:
    candidate = make_candidate(session, name="Ada Lovelace")
    company = make_company(session, name="Acme Inc.")
    job = make_job(
        session,
        company=company,
        title="Senior AI Engineer",
        location="Bangalore",
        employment_type="Full-time",
        experience_level="1-3 years",
    )
    analysis = JobAnalysis(
        job_id=job.id,
        prompt_version="job-analysis-v1",
        status=RunStatus.succeeded,
        analysis=ANALYSIS_PAYLOAD,
        model="gemini-3.8-flash",
    )
    session.add(analysis)
    session.flush()

    uploaded = upload(
        session,
        candidate,
        content=b"Five years building python agent pipelines with fastapi.",
    )

    envelope = JobMatchingAgent(FakeGeminiClient(MATCH)).match(
        session,
        job_id=job.id,
        candidate_id=candidate.id,
        resume_document_id=uploaded.document_id,
    )

    assert envelope["job_id"] == str(job.id)
    assert envelope["candidate_id"] == str(candidate.id)
    assert envelope["resume_document_id"] == str(uploaded.document_id)
    assert envelope["job_analysis_id"] == str(analysis.id)
    assert envelope["prompt_version"] == "job-matching-v1"
    assert envelope["match"] == MATCH.model_dump(mode="json")

    stored = session.scalar(select(Match).where(Match.resume_document_id == uploaded.document_id))
    assert stored is not None
    assert stored.candidate_id == candidate.id
    assert float(stored.score) == 70.0


def test_extract_resume_text_returns_normalized_text() -> None:
    assert extract_resume_text(b"  Single line  ") == "Single line"
    assert extract_resume_text(b"A\r\nB") == "A\nB"