"""Deterministic lifecycle of candidate profiles.

Every candidate profile belongs to exactly one account. The API never picks
"the" candidate: it resolves the one owned by the authenticated user, so two
accounts can never see each other's profile. Creating a profile is guarded to
one per user — the unique ``user_id`` is the database backstop.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate import Candidate

EDITABLE_FIELDS = frozenset(
    {
        "name",
        "headline",
        "location",
        "years_of_experience",
        "seniority",
        "skills",
        "summary",
    }
)


class DuplicateCandidateError(LookupError):
    """The account already owns a candidate profile."""


def candidate_for_user(session: Session, user_id: UUID) -> Candidate | None:
    """Return the profile owned by ``user_id``, or None.

    Looked up by the owner column rather than by creation order, so a second
    account's profile is never returned for the first.
    """
    stmt = select(Candidate).where(Candidate.user_id == user_id)
    return session.execute(stmt).scalar_one_or_none()


def create_candidate(
    session: Session,
    *,
    user_id: UUID,
    name: str,
    headline: str | None = None,
    location: str | None = None,
    years_of_experience: float | None = None,
    seniority: str | None = None,
    skills: list[str] | None = None,
    summary: str | None = None,
) -> Candidate:
    """Create the profile owned by ``user_id``, or fail when one exists.

    One profile per user keeps "the current candidate" unambiguous. The
    caller owns the transaction.
    """
    existing = candidate_for_user(session, user_id)
    if existing is not None:
        raise DuplicateCandidateError(
            f"account {user_id} already owns a candidate profile (id {existing.id})"
        )
    candidate = Candidate(
        user_id=user_id,
        name=name,
        headline=headline,
        location=location,
        years_of_experience=years_of_experience,
        seniority=seniority,
        skills=skills,
        summary=summary,
    )
    session.add(candidate)
    session.flush()
    session.refresh(candidate)
    return candidate


def update_candidate(
    session: Session,
    candidate: Candidate,
    *,
    updates: Mapping[str, object],
) -> Candidate:
    """Apply partial, whitelisted edits to ``candidate`` and flush.

    Only user-editable profile fields are written; identity and ownership
    columns (``id``, ``user_id``) are not in the whitelist and a key outside
    it is ignored, so a caller cannot overwrite ownership. Fields absent
    from ``updates`` are left untouched. The caller owns the transaction.
    """
    for field, value in updates.items():
        if field in EDITABLE_FIELDS:
            setattr(candidate, field, value)
    session.flush()
    session.refresh(candidate)
    return candidate
