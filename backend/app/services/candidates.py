"""Lifecycle of the single profile used by the local application."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.candidate import Candidate

LOCAL_CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000001")
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


class LocalProfileSelectionRequired(LookupError):
    """An upgrade retained multiple profiles and needs an explicit choice."""


def get_local_candidate(
    session: Session, *, create_if_missing: bool = True
) -> tuple[Candidate | None, bool]:
    """Return the selected local profile, promoting or creating only safely.

    The Boolean result says whether this call changed the database. The caller
    owns committing that initialization. A database with multiple unselected
    profiles is never resolved by creation order or by merging data.
    """
    candidate = session.execute(
        select(Candidate).where(Candidate.is_local_profile.is_(True))
    ).scalar_one_or_none()
    if candidate is not None:
        return candidate, False

    count = session.scalar(select(func.count()).select_from(Candidate)) or 0
    if count > 1:
        raise LocalProfileSelectionRequired(
            "Several candidate profiles were preserved during the upgrade. "
            "Choose the profile to use with "
            "`python -m app.cli.select_local_profile --list`, then "
            "`python -m app.cli.select_local_profile --candidate-id <ID>`. "
            "This keeps every profile and its related data without merging or deleting it."
        )
    if count == 1:
        candidate = session.execute(select(Candidate)).scalar_one()
        candidate.is_local_profile = True
        session.flush()
        return candidate, True
    if not create_if_missing:
        return None, False

    session.execute(
        insert(Candidate)
        .values(
            id=LOCAL_CANDIDATE_ID,
            name="",
            is_local_profile=True,
        )
        .on_conflict_do_nothing(
            index_elements=[Candidate.is_local_profile],
            index_where=Candidate.is_local_profile.is_(True),
        )
    )
    candidate = session.execute(
        select(Candidate).where(Candidate.is_local_profile.is_(True))
    ).scalar_one_or_none()
    if candidate is None:
        raise RuntimeError("the local profile could not be initialized")
    return candidate, candidate.id == LOCAL_CANDIDATE_ID


def select_local_candidate(session: Session, candidate_id: UUID) -> Candidate:
    """Explicitly choose a preserved profile without moving or deleting data."""
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        raise LookupError(f"candidate profile {candidate_id} does not exist")
    session.execute(
        update(Candidate)
        .where(Candidate.id != candidate_id)
        .values(is_local_profile=False)
        .execution_options(synchronize_session=False)
    )
    candidate.is_local_profile = True
    session.flush()
    return candidate


def update_candidate(
    session: Session,
    candidate: Candidate,
    *,
    updates: Mapping[str, object],
) -> Candidate:
    """Apply whitelisted profile edits and flush; the caller commits."""
    for field, value in updates.items():
        if field in EDITABLE_FIELDS:
            setattr(candidate, field, value)
    session.flush()
    session.refresh(candidate)
    return candidate
