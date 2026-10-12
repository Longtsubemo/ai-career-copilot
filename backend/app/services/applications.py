"""Application tracking as deliberate, user-driven records.

Applications are never created as a side effect of matching, analysis,
cover letter generation or resume upload: a row is only written when this
service is called. Every row is owned by exactly one candidate profile and
every read is scoped to that owner, so one account can never see or modify
another account's applications. The job title and company are snapshotted at
creation so a later job cleanup can never erase the fact of a submission, and
the status vocabulary is the shared ``ApplicationStatus`` enum — there is no
second state system.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.enums import ApplicationStatus
from app.models.job import Job

_UPDATABLE_FIELDS = frozenset({"status", "notes"})
_TRACK_LOCK_NAMESPACE = "application:job"


def _utcnow() -> datetime:
    """Return the current instant as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class ApplicationNotFoundError(LookupError):
    """The requested application id does not exist."""


class JobNotFoundError(LookupError):
    """The job referenced by an application does not exist."""


def get_application(
    session: Session, application_id: UUID, *, candidate_id: UUID
) -> Application:
    """Return one application owned by ``candidate_id``.

    A row owned by another candidate is reported exactly like an unknown id:
    ApplicationNotFoundError, so a caller can never learn that another
    account's application exists.
    """
    application = session.get(Application, application_id)
    if application is None or application.candidate_id != candidate_id:
        raise ApplicationNotFoundError(
            f"application {application_id} does not exist"
        )
    return application


def list_applications(
    session: Session,
    *,
    candidate_id: UUID,
    status: ApplicationStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Application], int]:
    """Return one page of a candidate's applications plus the total.

    Deterministic ordering: newest first, with the id as a tie-break.
    """
    filters = [Application.candidate_id == candidate_id]
    if status is not None:
        filters.append(Application.status == status)
    total = (
        session.scalar(
            select(func.count()).select_from(Application).where(*filters)
        )
        or 0
    )
    stmt = (
        select(Application)
        .where(*filters)
        .order_by(Application.created_at.desc(), Application.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = list(session.execute(stmt).scalars().all())
    return items, total


def find_application_for_job(
    session: Session, *, candidate_id: UUID, job_id: UUID
) -> Application | None:
    """Return the candidate's oldest application for ``job_id``, if any.

    Used to make tracking idempotent: a job the candidate already tracks must
    not gain a second record. The id is a deterministic tie-break when more
    than one row somehow exists.
    """
    stmt = (
        select(Application)
        .where(
            Application.candidate_id == candidate_id,
            Application.job_id == job_id,
        )
        .order_by(Application.created_at.asc(), Application.id.asc())
        .limit(1)
    )
    return session.scalar(stmt)


def _lock_job_tracking(
    session: Session, *, candidate_id: UUID, job_id: UUID
) -> None:
    """Serialize concurrent track requests for one candidate/job pair.

    A transaction-scoped PostgreSQL advisory lock makes the find-or-create in
    :func:`track_application` atomic across sessions: a second concurrent
    request waits, then sees the row the first one created instead of
    inserting a duplicate. Released when the caller's transaction ends.
    """
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:namespace), hashtext(:key))"),
        {
            "namespace": _TRACK_LOCK_NAMESPACE,
            "key": f"{candidate_id}:{job_id}",
        },
    )


def create_application(
    session: Session,
    *,
    candidate_id: UUID,
    job_id: UUID | None = None,
    status: ApplicationStatus = ApplicationStatus.draft,
    notes: str | None = None,
    now: datetime | None = None,
) -> Application:
    """Create one application record owned by ``candidate_id``.

    The caller resolves the candidate from the authenticated session, so a
    client can never choose the owner. When a job is referenced it must exist,
    and its title and company name are copied onto the application so the
    record outlives the job row.

    ``applied_at`` is never taken from the caller: creating with the
    ``applied`` status stamps the server's current UTC time, and any other
    status leaves it null. ``now`` exists only so tests can pin the clock.
    """
    title_snapshot: str | None = None
    company_snapshot: str | None = None
    if job_id is not None:
        job = session.get(Job, job_id)
        if job is None:
            raise JobNotFoundError(f"job {job_id} does not exist")
        title_snapshot = job.title
        company = job.company
        company_snapshot = company.name if company is not None else None

    application = Application(
        candidate_id=candidate_id,
        job_id=job_id,
        status=status,
        applied_at=(now or _utcnow())
        if status == ApplicationStatus.applied
        else None,
        notes=notes,
        job_title_snapshot=title_snapshot,
        company_name_snapshot=company_snapshot,
    )
    session.add(application)
    session.flush()
    session.refresh(application)
    return application


def track_application(
    session: Session,
    *,
    candidate_id: UUID,
    job_id: UUID,
    status: ApplicationStatus = ApplicationStatus.draft,
    notes: str | None = None,
    now: datetime | None = None,
) -> tuple[Application, bool]:
    """Idempotently create the candidate's tracking record for ``job_id``.

    Returns ``(application, created)``. A repeated request for a job this
    candidate already tracks returns the existing row with ``created=False``
    instead of inserting a duplicate, so "track this job" is safe to press
    twice. Concurrent requests are serialized by an advisory lock, so two
    racing callers still yield exactly one row. The caller owns the
    transaction.
    """
    _lock_job_tracking(session, candidate_id=candidate_id, job_id=job_id)
    existing = find_application_for_job(
        session, candidate_id=candidate_id, job_id=job_id
    )
    if existing is not None:
        return existing, False
    application = create_application(
        session,
        candidate_id=candidate_id,
        job_id=job_id,
        status=status,
        notes=notes,
        now=now,
    )
    return application, True


def update_application(
    session: Session,
    application_id: UUID,
    updates: Mapping[str, object],
    *,
    candidate_id: UUID,
    now: datetime | None = None,
) -> Application:
    """Apply a partial update to one of the candidate's applications.

    Only ``status`` and ``notes`` may be written; anything else is a
    programming error rather than a silent no-op. Ownership is enforced first,
    so another candidate's row is a 404.

    ``applied_at`` is server-owned and derived from the status transition:

    * a move into ``applied`` from any other status stamps the current server
      UTC time (an existing ``applied_at`` is overwritten, since the record is
      being (re)confirmed as submitted);
    * a move that leaves ``applied`` (to interview/offer/rejected/withdrawn)
      **preserves** ``applied_at`` — the fact that a submission happened is
      historical and must survive later stages;
    * a no-op status or a notes-only edit preserves ``applied_at`` unchanged.

    ``now`` exists only so tests can pin the clock.
    """
    unknown = set(updates) - _UPDATABLE_FIELDS
    if unknown:
        raise ValueError(
            f"unsupported application fields: {', '.join(sorted(unknown))}"
        )
    application = get_application(
        session, application_id, candidate_id=candidate_id
    )
    previous_status = application.status
    for field, value in updates.items():
        setattr(application, field, value)
    if (
        application.status == ApplicationStatus.applied
        and previous_status != ApplicationStatus.applied
    ):
        application.applied_at = now or _utcnow()
    session.flush()
    session.refresh(application)
    return application
