"""Application tracking endpoints: explicit records, never side effects.

Every route is scoped to the authenticated account's candidate: the owner is
resolved from the session, never from the request body, and a row belonging to
another account answers exactly like an unknown id.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_candidate
from app.api.schemas import (
    ApplicationCreateRequest,
    ApplicationListOut,
    ApplicationOut,
    ApplicationUpdateRequest,
)
from app.db.session import get_db
from app.models.application import Application
from app.models.candidate import Candidate
from app.models.enums import ApplicationStatus
from app.services import applications as applications_service

router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.post("", response_model=ApplicationOut, status_code=201)
def create_application(
    body: ApplicationCreateRequest,
    response: Response,
    session: Session = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
) -> Application:
    """Create one application record owned by the caller, snapshotting the job.

    A job-linked request is idempotent: when the candidate already tracks the
    job, the existing record is returned with ``200`` instead of inserting a
    duplicate. Only a genuinely new record answers ``201``. ``applied_at`` is
    never read from the body; the service stamps it when the status is
    ``applied``.
    """
    if body.job_id is not None:
        application, created = applications_service.track_application(
            session,
            candidate_id=candidate.id,
            job_id=body.job_id,
            status=body.status,
            notes=body.notes,
        )
        session.commit()
        if not created:
            response.status_code = 200
        return application

    application = applications_service.create_application(
        session,
        candidate_id=candidate.id,
        status=body.status,
        notes=body.notes,
    )
    session.commit()
    return application


@router.get("", response_model=ApplicationListOut)
def list_applications(
    session: Session = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
    status: ApplicationStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApplicationListOut:
    """Return a page of the caller's applications with the total count."""
    items, total = applications_service.list_applications(
        session,
        candidate_id=candidate.id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return ApplicationListOut(items=items, total=total, limit=limit, offset=offset)


@router.get("/{application_id}", response_model=ApplicationOut)
def read_application(
    application_id: UUID,
    session: Session = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
) -> Application:
    """Return one of the caller's applications, or 404 when it does not exist."""
    return applications_service.get_application(
        session, application_id, candidate_id=candidate.id
    )


@router.patch("/{application_id}", response_model=ApplicationOut)
def update_application(
    application_id: UUID,
    body: ApplicationUpdateRequest,
    session: Session = Depends(get_db),
    candidate: Candidate = Depends(get_current_candidate),
) -> Application:
    """Apply a partial update to the caller's application; empty body is a 422."""
    updates = {name: getattr(body, name) for name in body.model_fields_set}
    application = applications_service.update_application(
        session, application_id, updates, candidate_id=candidate.id
    )
    session.commit()
    return application


__all__ = ["router"]
