"""Deterministic ingestion of user-uploaded master resumes.

Resumes are manual application input: the user owns the content and there is
no Resume Agent. Uploading a resume validates input, extracts a normalized
text representation from the provided bytes and appends a new ``Document`` of
kind ``master_resume`` — the schema's source-resume kind — to the owning
candidate. Nothing is committed here; the caller owns the transaction.

A master resume is source evidence: it is never overwritten. A new upload
inserts a *new* row whose ``source_document_id`` points at the previous master
resume, so every version stays comparable and Job Matching keeps reading a
stable document. The service never calls Gemini and never creates an
``AgentRun`` — resume upload is determinable application functionality, not an
agent task.

Accepted formats are plain text, Markdown, PDF and DOCX. Extraction is local
and LLM-free (``pypdf`` for PDF, ``python-docx`` for DOCX); no document is ever
sent to an external service. The declared extension and content type are
validated, but the bytes are sniffed too, so a mislabelled file is rejected
rather than silently stored. Scanned image-only PDFs are not supported: OCR is
unavailable, which is reported as an actionable error instead of an empty
resume.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from uuid import UUID

from docx import Document as _open_docx
from docx.document import Document as _DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table as _DocxTable, _Cell
from docx.text.paragraph import Paragraph as _DocxParagraph
from pydantic import BaseModel, Field
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate import Candidate
from app.models.document import Document
from app.models.enums import DocumentKind

MAX_RESUME_BYTES = 2 * 1024 * 1024

_TEXT = "text"
_PDF = "pdf"
_DOCX = "docx"
_OLE = "ole"

_TEXT_CONTENT_TYPES = frozenset({"text/plain", "text/markdown"})
_PDF_CONTENT_TYPES = frozenset({"application/pdf", "application/x-pdf"})
_DOCX_CONTENT_TYPES = frozenset(
    {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
)
_SUPPORTED_CONTENT_TYPES = _TEXT_CONTENT_TYPES | _PDF_CONTENT_TYPES | _DOCX_CONTENT_TYPES

_TEXT_EXTENSIONS = frozenset({".txt", ".text", ".md", ".markdown"})
_PDF_EXTENSIONS = frozenset({".pdf"})
_DOCX_EXTENSIONS = frozenset({".docx"})
_SUPPORTED_EXTENSIONS = _TEXT_EXTENSIONS | _PDF_EXTENSIONS | _DOCX_EXTENSIONS

_PDF_MAGIC = b"%PDF-"

_ZIP_MAGIC = b"PK\x03\x04"

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class CandidateNotFoundError(LookupError):
    """The requested candidate id does not exist in the candidates table."""


class InvalidResumeError(ValueError):
    """Resume input failed one of the deterministic upload validations."""


class EmptyResumeError(InvalidResumeError):
    """The uploaded resume carried no meaningful text content."""


class ImageOnlyResumeError(EmptyResumeError):
    """The document had no selectable text; OCR is not available in this phase."""


class UnsupportedResumeFormatError(InvalidResumeError):
    """The uploaded resume was not a supported, text-representable format."""


class EncryptedResumeError(InvalidResumeError):
    """The uploaded document is password-protected and cannot be extracted."""


class OversizedResumeError(InvalidResumeError):
    """The uploaded resume exceeded the maximum accepted size."""


class ResumeUploadResult(BaseModel):
    """Stable result of a successful master resume upload."""

    candidate_id: UUID
    document_id: UUID
    kind: DocumentKind = Field(default=DocumentKind.master_resume)
    filename: str | None = None
    created_at: datetime


def upload_master_resume(
    session: Session,
    *,
    candidate_id: UUID,
    content: bytes,
    filename: str | None = None,
    content_type: str | None = None,
    max_bytes: int = MAX_RESUME_BYTES,
) -> ResumeUploadResult:
    """Validate, normalize and append one master resume for a candidate.

    A new upload never mutates an existing document: the previous master
    resume remains untouched and the fresh row is linked to it through
    ``source_document_id``, preserving version lineage without any schema
    change. Extraction is deterministic and LLM-free.
    """
    if session.get(Candidate, candidate_id) is None:
        raise CandidateNotFoundError(f"candidate {candidate_id} does not exist")

    if not isinstance(content, bytes) or not content:
        raise EmptyResumeError("resume content must be non-empty bytes")

    if filename is not None and not filename.strip():
        raise InvalidResumeError("filename must not be blank")

    if len(content) > max_bytes:
        raise OversizedResumeError(
            f"resume content exceeds the {max_bytes} byte limit"
        )

    text = extract_resume_text(
        content, content_type=content_type, filename=filename
    )
    if not text:
        raise EmptyResumeError("resume content must contain readable text")

    previous_id = _previous_master_resume_id(session, candidate_id)
    document = Document(
        kind=DocumentKind.master_resume,
        candidate_id=candidate_id,
        source_document_id=previous_id,
        content=text,
        model=None,
        prompt_version=None,
        job_id=None,
        application_id=None,
        agent_run_id=None,
    )
    session.add(document)
    session.flush()
    session.refresh(document)

    return ResumeUploadResult(
        candidate_id=candidate_id,
        document_id=document.id,
        kind=document.kind,
        filename=filename.strip() if filename else None,
        created_at=document.created_at,
    )


def current_master_resume(session: Session, candidate_id: UUID) -> Document | None:
    """Return the newest master resume of a candidate, or None.

    Newest means ``created_at DESC, id DESC``, the same order the version
    chain is built in, and only ``master_resume`` rows qualify — cover
    letters and tailored resumes are never returned by this lookup.
    """
    stmt = (
        select(Document)
        .where(
            Document.candidate_id == candidate_id,
            Document.kind == DocumentKind.master_resume,
        )
        .order_by(Document.created_at.desc(), Document.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def master_resume_history(session: Session, candidate_id: UUID) -> list[Document]:
    """Return every master resume of a candidate, newest first.

    The full version chain, including rows superseded by a later upload:
    nothing is deleted or rewritten when a new version arrives.
    """
    stmt = (
        select(Document)
        .where(
            Document.candidate_id == candidate_id,
            Document.kind == DocumentKind.master_resume,
        )
        .order_by(Document.created_at.desc(), Document.id.desc())
    )
    return list(session.execute(stmt).scalars().all())


def extract_resume_text(
    content: bytes,
    *,
    content_type: str | None = None,
    filename: str | None = None,
) -> str:
    """Return the normalized text representation of raw resume bytes.

    The format is resolved from the declared extension and content type and
    cross-checked against the bytes themselves, so a mislabelled or corrupted
    file is rejected rather than silently stored as text. Text and Markdown
    decode as UTF-8 (BOM stripped); PDF and DOCX are extracted locally. An
    empty or unreadable document raises a specific error the HTTP layer maps
    to an actionable client message.
    """
    kind = _resolve_kind(content, content_type=content_type, filename=filename)
    if kind == _PDF:
        return _extract_pdf(content)
    if kind == _DOCX:
        return _extract_docx(content)
    return _extract_text(content)


def _resolve_kind(
    content: bytes, *, content_type: str | None, filename: str | None
) -> str:
    declared = _normalize_content_type(content_type)
    if declared is not None and declared not in _SUPPORTED_CONTENT_TYPES:
        raise UnsupportedResumeFormatError(
            f"unsupported content type {content_type!r}; expected one of "
            f"{sorted(_SUPPORTED_CONTENT_TYPES)}"
        )

    extension = _normalize_extension(filename)
    if extension is not None and extension not in _SUPPORTED_EXTENSIONS:
        raise UnsupportedResumeFormatError(
            f"unsupported file extension {extension!r}; expected one of "
            f"{sorted(_SUPPORTED_EXTENSIONS)}"
        )

    declared_kind = _kind_for_content_type(declared)
    extension_kind = _kind_for_extension(extension)
    if (
        declared_kind is not None
        and extension_kind is not None
        and declared_kind != extension_kind
    ):
        raise UnsupportedResumeFormatError(
            "the file extension and content type disagree; that combination "
            "is not supported"
        )

    expected = declared_kind or extension_kind
    sniffed = _sniff_kind(content)

    if sniffed == _OLE:
        raise EncryptedResumeError(
            "the document looks like an encrypted or legacy Word file; only "
            "DOCX is supported"
        )

    if expected is None:
        return sniffed or _TEXT

    if expected == _TEXT:
        if sniffed in (_PDF, _DOCX):
            raise UnsupportedResumeFormatError(
                "the file content does not match its extension or content "
                "type; that combination is not supported"
            )
        return _TEXT

    if sniffed != expected:
        raise UnsupportedResumeFormatError(
            "the file content does not match its extension or content type; "
            "that combination is not supported"
        )
    return expected


def _normalize_content_type(content_type: str | None) -> str | None:
    if content_type is None:
        return None
    declared = content_type.split(";")[0].strip().lower()
    return declared or None


def _normalize_extension(filename: str | None) -> str | None:
    if not filename or not filename.strip():
        return None
    name = filename.strip().lower()
    dot = name.rfind(".")
    if dot < 0:
        return None
    return name[dot:]


def _kind_for_content_type(declared: str | None) -> str | None:
    if declared is None:
        return None
    if declared in _TEXT_CONTENT_TYPES:
        return _TEXT
    if declared in _PDF_CONTENT_TYPES:
        return _PDF
    if declared in _DOCX_CONTENT_TYPES:
        return _DOCX
    return None


def _kind_for_extension(extension: str | None) -> str | None:
    if extension is None:
        return None
    if extension in _TEXT_EXTENSIONS:
        return _TEXT
    if extension in _PDF_EXTENSIONS:
        return _PDF
    if extension in _DOCX_EXTENSIONS:
        return _DOCX
    return None


def _sniff_kind(content: bytes) -> str | None:
    head = content[:1024]
    if _PDF_MAGIC in head:
        return _PDF
    if head.startswith(_ZIP_MAGIC):
        return _DOCX
    if head.startswith(_OLE_MAGIC):
        return _OLE
    return None


def _extract_text(content: bytes) -> str:
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UnsupportedResumeFormatError(
            "resume content is not readable text; upload a TXT, Markdown, PDF "
            "or DOCX file"
        ) from exc
    return _normalize_text(decoded)


def _extract_pdf(content: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(content))
    except Exception as exc:
        raise UnsupportedResumeFormatError(
            "the PDF file is corrupted or could not be read"
        ) from exc

    if reader.is_encrypted:
        raise EncryptedResumeError(
            "the PDF is encrypted; remove the password and upload it again"
        )

    try:
        chunks = [
            page.extract_text() or "" for page in reader.pages
        ]
    except Exception as exc:
        raise UnsupportedResumeFormatError(
            "the PDF file is corrupted or could not be read"
        ) from exc

    text = _normalize_text("\n".join(chunk for chunk in chunks if chunk.strip()))
    if not text:
        raise ImageOnlyResumeError(
            "no selectable text was found in the PDF; scanned or image-only "
            "PDFs need OCR, which is not available"
        )
    return text


def _extract_docx(content: bytes) -> str:
    try:
        document = _open_docx(BytesIO(content))
    except Exception as exc:
        raise UnsupportedResumeFormatError(
            "the DOCX file is corrupted or could not be read"
        ) from exc

    lines: list[str] = []
    try:
        for block in _iter_docx_blocks(document):
            if isinstance(block, _DocxParagraph):
                text = block.text.strip()
                if text:
                    lines.append(text)
            else:
                for row in block.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        lines.append(" | ".join(cells))
    except Exception as exc:
        raise UnsupportedResumeFormatError(
            "the DOCX file is corrupted or could not be read"
        ) from exc

    text = _normalize_text("\n".join(lines))
    if not text:
        raise EmptyResumeError("the DOCX file did not contain any readable text")
    return text


def _iter_docx_blocks(parent):
    """Yield paragraphs and tables of a DOCX body in document order."""
    if isinstance(parent, _DocxDocument):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise UnsupportedResumeFormatError("unsupported DOCX structure")
    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield _DocxParagraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield _DocxTable(child, parent)


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def _previous_master_resume_id(
    session: Session, candidate_id: UUID
) -> UUID | None:
    stmt = (
        select(Document.id)
        .where(
            Document.candidate_id == candidate_id,
            Document.kind == DocumentKind.master_resume,
        )
        .order_by(Document.created_at.desc(), Document.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()
