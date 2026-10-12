# Phase 1F — PDF and DOCX Resume Upload Support

## Summary

The master-resume upload workflow now accepts PDF and Microsoft Word DOCX
files in addition to the existing TXT and Markdown support. Extraction is
performed locally in the backend; no document is ever sent to an external
service. Upload validation, master-resume version history, append-only
behavior and authentication/ownership are unchanged.

## Files changed

### Backend

- `backend/app/services/resume_upload.py` — added format resolution
  (extension + declared content type + byte sniffing) and local extraction for
  PDF (`pypdf`) and DOCX (`python-docx`, paragraphs and table text in document
  order). New exceptions: `ImageOnlyResumeError`, `EncryptedResumeError`.
  `extract_resume_text()` gained an optional `filename` argument; the upload
  signature and `ResumeUploadResult` are unchanged.
- `backend/app/api/errors.py` — registered the two new domain handlers
  (`EncryptedResumeError` → 422, `ImageOnlyResumeError` → 400).
- `backend/tests/resume_fixtures.py` — new. Locally generated, deterministic
  fixtures: a minimal hand-built PDF writer, an encrypted-PDF builder and a
  DOCX builder (paragraphs + table). No real resumes, no network.
- `backend/tests/test_resume_upload.py` — extended the service suite; the two
  obsolete "PDF/DOCX are rejected" assertions were replaced with real
  extraction tests and rejection tests for the remaining unsupported cases.
- `backend/tests/api/test_api_resumes.py` — extended the HTTP suite with
  multipart PDF/DOCX uploads, extraction, history, error-mapping,
  unauthenticated-request and cross-user ownership coverage.

### Frontend

- `frontend/src/utils/resumeUpload.ts` — new dependency-free helper:
  supported extensions, accept attribute, format label, extension parsing,
  advertised-content-type mapping and a supported-file-name check.
- `frontend/src/api/resumes.ts` — upload now declares the correct content type
  for `.pdf` / `.docx` instead of always forcing a text type.
- `frontend/src/pages/ResumePage.tsx` — accept attribute and help/empty text
  now list TXT, Markdown, PDF and DOCX; the 2 MB limit stays visible; an
  unsupported extension is rejected client-side with a clear message.
- `frontend/tests/resumeUpload.test.ts` — new tests for content-type mapping,
  supported formats and the accept affordances (Node's built-in test runner).

## Extraction libraries

| Format | Library | Notes |
| --- | --- | --- |
| PDF | `pypdf>=5.1.0,<7.0.0` (installed 6.20.0) | `PdfReader`; per-page `extract_text()` |
| DOCX | `python-docx>=1.1.2,<2.0.0` | Body paragraphs and table rows in document order |
| TXT / Markdown | Python stdlib | UTF-8 (`utf-8-sig`, BOM stripped) |

Both libraries were already listed in `backend/requirements.txt`
(uncommitted working-tree state) and are installed in the checked-in venv, so
no dependency change was needed for this phase and no schema change was made.

## Supported formats and validation

Accepted: `.txt`, `.text`, `.md`, `.markdown`, `.pdf`, `.docx`.
Content types accepted: `text/plain`, `text/markdown`, `application/pdf`,
`application/x-pdf`, and the OOXML WordprocessingML type.

Resolution logic:

1. A declared extension outside the supported set is rejected.
2. A declared content type outside the supported set is rejected.
3. If both a declared extension and a declared content type are present and
   they disagree about the format family, the request is rejected.
4. The raw bytes are sniffed (PDF `%PDF-` magic, ZIP `PK\x03\x04`, OLE
   `D0 CF 11 E0`). Content that contradicts the expected family is rejected, so
   the extension alone is never trusted.
5. Extraction must yield non-blank text; otherwise a specific error is raised.

The 2 MB `MAX_RESUME_BYTES` limit and the blank-filename / empty-bytes checks
are unchanged. The route still reads at most `MAX_RESUME_BYTES + 1` bytes and
never accepts a client-supplied candidate id or owner.

## Error behavior

| Condition | Exception | HTTP |
| --- | --- | --- |
| Unsupported extension / content type / content mismatch | `UnsupportedResumeFormatError` | 415 |
| Corrupted / unreadable PDF or DOCX | `UnsupportedResumeFormatError` | 415 |
| Encrypted PDF (and OLE/legacy Word bytes) | `EncryptedResumeError` | 422 |
| Image-only / scanned PDF (no selectable text) | `ImageOnlyResumeError` | 400 |
| Blank extracted text | `EmptyResumeError` | 400 |
| Over 2 MB | `OversizedResumeError` | 413 |
| Blank filename | `InvalidResumeError` | 400 |

Error bodies are the existing `{"detail": "<message>"}` shape; no traceback,
stack frame or file content is ever returned to a client. The scanned-PDF
message states that OCR is unavailable rather than silently accepting an empty
resume.

## Storage and version history

Unchanged. A successful extraction is persisted through the same service and
`Document` model: one `master_resume` row per upload, chained to the previous
version via `source_document_id` (newest first), flushes but never commits
(the caller owns the transaction), and belongs to the authenticated user's
candidate. No uploaded file bytes are persisted and no migration was added.

## Verification

All run from `backend/` with the venv active:

- Focused: `python -m pytest tests/test_resume_upload.py tests/api/test_api_resumes.py -q`
  → **49 passed** (26 service + 23 API).
- Full suite: `python -m pytest -q` → **747 passed**.
- `python -m alembic check` → **No new upgrade operations detected.**

From `frontend/`:

- `npm test` → **41 passed**.
- `npm run build` (`tsc -b && vite build`) → **success**.

## Limitations

- **No OCR.** Scanned/image-only PDFs are rejected with an explicit message;
  they are not stored as empty resumes.
- **No legacy `.doc` and no `.rtf`.** Only OOXML `.docx` is supported; OLE
  (`D0 CF 11 E0`) content is reported as encrypted/legacy Word.
- Extraction preserves reading order and strips trailing whitespace, but it is
  text extraction only — no layout, columns, images, headers/footers or
  annotations are reconstructed.
- The frontend maps file extensions to content types; a file whose real format
  disagrees with its extension is rejected by the backend (415), not silently
  coerced.

## Manual smoke-test checklist

1. `docker start career-copilot-db`; from `backend/`, run the app and
   `alembic upgrade head`.
2. `cd frontend && npm run dev`, open the Resume page.
3. Upload a `.txt` and confirm the current resume and version history update.
4. Upload a text-based `.pdf` (paragraphs preserved) and confirm extraction.
5. Upload a `.docx` containing paragraphs and a table; confirm table text is
   present.
6. Upload a scanned/image-only PDF; expect a 400 with an OCR explanation and no
   new version.
7. Upload an encrypted PDF; expect a 422 password message.
8. Upload a renamed file (text content with a `.pdf` name); expect a 415.
9. Upload a file over 2 MB; expect the size error and no new version.
10. Confirm unsupported types are blocked and the help text/`accept` list all
    four formats.
