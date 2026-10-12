"""Candidate profile endpoints: the person the copilot works for.

Every endpoint is scoped to the authenticated account. The list returns only
that account's profile, "me" resolves the profile it owns, and a create is
rejected once the account already owns one — a client can never name another
user's profile.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_candidate, get_current_user
from app.api.schemas import (
    CandidateCreateRequest,
    CandidateOut,
    CandidateUpdateRequest,
)
from app.db.session import get_db
from app.models.candidate import Candidate
from app.models.user import User
from app.services import candidates as candidate_service

router = APIRouter(prefix="/api/candidates", tags=["candidates"])


@router.get("", response_model=list[CandidateOut])
def list_candidates(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[Candidate]:
    """Return the authenticated account's own profile (at most one)."""
    candidate = candidate_service.candidate_for_user(session, user.id)
    return [candidate] if candidate is not None else []


@router.get("/me", response_model=CandidateOut)
def read_current_candidate(
    candidate: Candidate = Depends(get_current_candidate),
) -> Candidate:
    """Return the profile owned by the authenticated account."""
    return candidate


@router.post("", response_model=CandidateOut, status_code=201)
def create_candidate(
    body: CandidateCreateRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> Candidate:
    """Create the account's profile; a second one is a 409.

    The owner is always the authenticated account — the body cannot name a
    user, so a profile can never be created for someone else.
    """
    candidate = candidate_service.create_candidate(
        session,
        user_id=user.id,
        name=body.name,
        headline=body.headline,
        location=body.location,
        years_of_experience=body.years_of_experience,
        seniority=body.seniority,
        skills=body.skills,
        summary=body.summary,
    )
    session.commit()
    return candidate


@router.patch("/me", response_model=CandidateOut)
def update_current_candidate(
    body: CandidateUpdateRequest,
    candidate: Candidate = Depends(get_current_candidate),
    session: Session = Depends(get_db),
) -> Candidate:
    """Apply a partial edit to the authenticated account's own profile.

    The target is always the session's candidate, so the body can never
    name a different owner. Unknown or identity fields and an empty body
    are rejected by the schema (422), and fields left out are preserved.
    """
    updates = {name: getattr(body, name) for name in body.model_fields_set}
    updated = candidate_service.update_candidate(session, candidate, updates=updates)
    session.commit()
    return updated


__all__ = ["router"]
