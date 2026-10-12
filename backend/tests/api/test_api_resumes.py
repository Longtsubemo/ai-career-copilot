"""Behaviour of the master resume endpoints."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.api.deps import NO_CANDIDATE_DETAIL, NO_MASTER_RESUME_DETAIL
from app.models.document import Document
from app.models.enums import DocumentKind
from app.services.resume_upload import MAX_RESUME_BYTES
from tests.api.support import (
    RESUME_TEXT,
    build_candidate,
    login,
    make_user_client,
    register,
)
from tests.resume_fixtures import (
    make_docx_bytes,
    make_encrypted_pdf_bytes,
    make_pdf_bytes,
)

_DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _sign_up(client) -> str:
    register(client)
    response = client.get("/api/candidates/me")
    assert response.status_code == 200
    return response.json()["id"]


def test_upload_requires_authentication(client, session):
    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.txt", b"experience", "text/plain")},
    )

    assert response.status_code == 401
    assert session.execute(select(Document)).scalars().all() == []


def test_upload_requires_a_candidate(client_factory, session):
    client, _ = make_user_client(client_factory, session)

    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.txt", b"experience", "text/plain")},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == NO_CANDIDATE_DETAIL
    assert session.execute(select(Document)).scalars().all() == []


def test_upload_then_read_current_master_resume(client, session):
    candidate_id = _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.txt", RESUME_TEXT.encode(), "text/plain")},
    )

    assert response.status_code == 201
    uploaded = response.json()
    assert uploaded["candidate_id"] == candidate_id
    assert uploaded["kind"] == "master_resume"
    assert uploaded["filename"] == "resume.txt"

    read = client.get("/api/resumes/master")
    assert read.status_code == 200
    assert read.json()["id"] == uploaded["document_id"]
    assert read.json()["content"] == RESUME_TEXT

    document = session.get(Document, UUID(uploaded["document_id"]))
    assert document is not None
    assert document.kind is DocumentKind.master_resume
    assert document.candidate_id == UUID(candidate_id)
    assert document.source_document_id is None
    assert document.model is None
    assert document.prompt_version is None


def test_read_before_first_upload_is_404(client):
    _sign_up(client)

    response = client.get("/api/resumes/master")

    assert response.status_code == 404
    assert response.json()["detail"] == NO_MASTER_RESUME_DETAIL


def test_upload_normalizes_line_endings_and_whitespace(client):
    _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={
            "file": (
                "resume.txt",
                "  First line\r\nSecond line  ".encode(),
                "text/plain",
            )
        },
    )

    assert response.status_code == 201
    assert client.get("/api/resumes/master").json()["content"] == (
        "First line\nSecond line"
    )


def test_each_upload_appends_a_new_version(client, session):
    _sign_up(client)
    first = client.post(
        "/api/resumes/master",
        files={"file": ("v1.txt", b"version one", "text/plain")},
    ).json()
    second = client.post(
        "/api/resumes/master",
        files={"file": ("v2.txt", b"version two", "text/plain")},
    ).json()

    history = client.get("/api/resumes/master/history")

    assert history.status_code == 200
    body = history.json()
    assert body["count"] == 2
    assert {row["id"] for row in body["items"]} == {
        first["document_id"],
        second["document_id"],
    }
    assert {row["content"] for row in body["items"]} == {"version one", "version two"}

    chained = session.get(Document, UUID(second["document_id"]))
    assert chained.source_document_id == UUID(first["document_id"])
    original = session.get(Document, UUID(first["document_id"]))
    assert original.content == "version one"
    assert original.source_document_id is None


def test_history_before_any_upload_is_empty(client):
    _sign_up(client)

    response = client.get("/api/resumes/master/history")

    assert response.status_code == 200
    assert response.json() == {"items": [], "count": 0}


def test_binary_resume_is_rejected(client, session):
    _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.txt", b"PK\x03\x04binary", "text/plain")},
    )

    assert response.status_code == 415
    assert "not supported" in response.json()["detail"].lower()
    assert session.execute(select(Document)).scalars().all() == []


def test_pdf_bytes_are_rejected_even_with_a_text_content_type(client):
    _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.txt", b"%PDF-1.7 opaque", "text/plain")},
    )

    assert response.status_code == 415


def test_empty_resume_is_rejected(client, session):
    _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.txt", b"   \n  ", "text/plain")},
    )

    assert response.status_code == 400
    assert session.execute(select(Document)).scalars().all() == []


def test_oversized_resume_is_rejected(client, session):
    _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={
            "file": (
                "resume.txt",
                b"a" * (MAX_RESUME_BYTES + 1),
                "text/plain",
            )
        },
    )

    assert response.status_code == 413
    assert session.execute(select(Document)).scalars().all() == []


def test_blank_filename_is_rejected(client):
    _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={"file": ("   ", b"content", "text/plain")},
    )

    assert response.status_code == 400


def test_markdown_upload_is_accepted(client):
    _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.md", b"# Ada\nEngineer", "text/markdown")},
    )

    assert response.status_code == 201
    assert client.get("/api/resumes/master").json()["content"] == "# Ada\nEngineer"


def test_pdf_upload_extracts_text(client):
    _sign_up(client)
    pdf = make_pdf_bytes("Ada Lovelace", "Backend Engineer")

    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.pdf", pdf, "application/pdf")},
    )

    assert response.status_code == 201
    assert response.json()["filename"] == "resume.pdf"
    assert client.get("/api/resumes/master").json()["content"] == (
        "Ada Lovelace\nBackend Engineer"
    )


def test_docx_upload_extracts_paragraphs_and_table(client):
    _sign_up(client)
    docx = make_docx_bytes(
        ["Ada Lovelace"], [["Skill", "Years"], ["Python", "5"]]
    )

    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.docx", docx, _DOCX_MIME)},
    )

    assert response.status_code == 201
    assert client.get("/api/resumes/master").json()["content"] == (
        "Ada Lovelace\nSkill | Years\nPython | 5"
    )


def test_pdf_and_docx_uploads_append_to_history(client):
    _sign_up(client)
    plain = client.post(
        "/api/resumes/master",
        files={"file": ("v1.txt", b"plain version", "text/plain")},
    ).json()
    pdf = client.post(
        "/api/resumes/master",
        files={"file": ("v2.pdf", make_pdf_bytes("pdf version"), "application/pdf")},
    ).json()
    docx = client.post(
        "/api/resumes/master",
        files={"file": ("v3.docx", make_docx_bytes(["docx version"]), _DOCX_MIME)},
    ).json()

    history = client.get("/api/resumes/master/history").json()

    assert history["count"] == 3
    assert {row["id"] for row in history["items"]} == {
        plain["document_id"],
        pdf["document_id"],
        docx["document_id"],
    }
    assert {row["content"] for row in history["items"]} == {
        "plain version",
        "pdf version",
        "docx version",
    }


def test_scanned_pdf_is_rejected_with_an_ocr_message(client, session):
    _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={"file": ("scan.pdf", make_pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 400
    assert "ocr" in response.json()["detail"].lower()
    assert session.execute(select(Document)).scalars().all() == []


def test_encrypted_pdf_is_rejected(client, session):
    _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={
            "file": (
                "locked.pdf",
                make_encrypted_pdf_bytes("hidden"),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 422
    assert session.execute(select(Document)).scalars().all() == []


def test_corrupted_pdf_is_rejected(client, session):
    _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={"file": ("broken.pdf", b"%PDF-1.7 broken", "application/pdf")},
    )

    assert response.status_code == 415
    assert session.execute(select(Document)).scalars().all() == []


def test_unsupported_extension_is_rejected(client):
    _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.rtf", b"payload", "text/plain")},
    )

    assert response.status_code == 415


def test_pdf_that_does_not_match_its_content_is_rejected(client):
    _sign_up(client)

    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.pdf", b"plain text", "application/pdf")},
    )

    assert response.status_code == 415


def test_upload_requires_authentication_for_pdf(client, session):
    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.pdf", make_pdf_bytes("Ada"), "application/pdf")},
    )

    assert response.status_code == 401
    assert session.execute(select(Document)).scalars().all() == []


def test_a_pdf_upload_belongs_to_the_authenticated_candidate(
    client_factory, session
):
    account = build_candidate(session, name="Ada")
    other = build_candidate(session, name="Grace")
    client = client_factory()
    login(client, account.user)

    response = client.post(
        "/api/resumes/master",
        files={"file": ("resume.pdf", make_pdf_bytes("Ada"), "application/pdf")},
    )

    assert response.status_code == 201
    document = session.get(Document, UUID(response.json()["document_id"]))
    assert document is not None
    assert document.candidate_id == account.id
    assert document.candidate_id != other.id
