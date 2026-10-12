"""Master resume endpoints: upload, current version and version history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import NO_MASTER_RESUME_DETAIL, get_current_candidate
from app.api.schemas import ResumeHistoryOut, ResumeOut, ResumeUploadOut
from app.db.session import get_db
from app.models.candidate import Candidate
from app.models.document import Document
from app.services import resume_upload

router = APIRouter(prefix="/api/resumes", tags=["resumes"])


@router.post("/master", response_model=ResumeUploadOut, status_code=201)
async def upload_master_resume(
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
) -> ResumeUploadOut:
    """Append a new master resume version for the current candidate.

    At most ``MAX_RESUME_BYTES + 1`` bytes are read from the stream, so an
    oversized upload is rejected by the service without buffering the
    whole file in memory.
    """
    content = await file.read(resume_upload.MAX_RESUME_BYTES + 1)
    result = resume_upload.upload_master_resume(
        session,
        candidate_id=candidate.id,
        content=content,
        filename=file.filename,
        content_type=file.content_type,
    )
    session.commit()
    return ResumeUploadOut.model_validate(result)


@router.get("/master", response_model=ResumeOut)
def read_master_resume(
    session: Session = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
) -> Document:
    """Return the current master resume, or 404 before the first upload."""
    document = resume_upload.current_master_resume(session, candidate.id)
    if document is None:
        raise HTTPException(status_code=404, detail=NO_MASTER_RESUME_DETAIL)
    return document


@router.get("/master/history", response_model=ResumeHistoryOut)
def read_master_resume_history(
    session: Session = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
) -> ResumeHistoryOut:
    """Return every master resume version, newest first."""
    items = resume_upload.master_resume_history(session, candidate.id)
    return ResumeHistoryOut(items=items, count=len(items))


__all__ = ["router"]
