"""Read and edit the local application profile."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_candidate
from app.api.schemas import CandidateOut, CandidateUpdateRequest
from app.db.session import get_db
from app.models.candidate import Candidate
from app.services import candidates as candidate_service

router = APIRouter(prefix="/api/candidates", tags=["profile"])


@router.get("/me", response_model=CandidateOut)
def read_current_candidate(
    candidate: Candidate = Depends(get_current_candidate),
) -> Candidate:
    """Return the persistent local profile, initializing it on first use."""
    return candidate


@router.patch("/me", response_model=CandidateOut)
def update_current_candidate(
    body: CandidateUpdateRequest,
    candidate: Candidate = Depends(get_current_candidate),
    session: Session = Depends(get_db),
) -> Candidate:
    """Apply a partial edit to the local profile."""
    updates = {name: getattr(body, name) for name in body.model_fields_set}
    updated = candidate_service.update_candidate(session, candidate, updates=updates)
    session.commit()
    return updated


__all__ = ["router"]
