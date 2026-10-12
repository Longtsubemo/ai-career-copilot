"""Job listing, lookup and explicit re-verification over postings.

Reading is shared and unauthenticated; re-verification is a state-changing
user action (it updates the shared job row after crawling its source URL), so
it requires an authenticated session and is rate-limited by a per-job
cooldown.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.agents.runtime.runtime import utc_now
from app.api.deps import get_current_user, get_firecrawl
from app.api.schemas import JobListOut, JobOut, JobVerificationOut
from app.clients.firecrawl import FirecrawlClient
from app.core.config import get_settings
from app.db.session import get_db
from app.models.enums import AvailabilityStatus
from app.models.job import Job
from app.models.user import User
from app.services import jobs as jobs_service
from app.services.freshness import resolve_availability
from app.services.job_verification import verify_job_availability

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _job_out(job: Job, *, now=None) -> JobOut:
    """Render one job with its company name and effective availability.

    ``now`` is taken once per request and shared with the list filter so a
    row at the freshness boundary is filtered and labelled the same way.
    """
    if now is None:
        now = utc_now()
    settings = get_settings()
    out = JobOut.model_validate(job)
    out.company_name = job.company.name if job.company is not None else None
    out.last_verified_at = job.crawled_at
    out.availability = resolve_availability(
        job.availability,
        job.crawled_at,
        now=now,
        threshold_days=settings.job_freshness_days,
    )
    return out


@router.get("", response_model=JobListOut)
def list_jobs(
    session: Session = Depends(get_db),
    location: str | None = Query(default=None),
    experience_level: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    source: str | None = Query(default=None),
    availability: AvailabilityStatus | None = Query(default=None),
    limit: int = Query(default=jobs_service.DEFAULT_LIMIT, ge=1, le=jobs_service.MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> JobListOut:
    """Return a page of jobs with the total number of matches.

    Filters are plain column predicates: ``location`` is a
    case-insensitive substring match, the rest are equality. No salary,
    currency or semantic filtering exists in the product. The availability
    filter accepts every effective state (``available``, ``stale``,
    ``closed``, ``unknown``) and is applied before the total is counted.
    """
    now = utc_now()
    settings = get_settings()
    items, total = jobs_service.list_jobs(
        session,
        location=location,
        experience_level=experience_level,
        is_active=is_active,
        source=source,
        availability=availability,
        now=now,
        threshold_days=settings.job_freshness_days,
        limit=limit,
        offset=offset,
    )
    return JobListOut(
        items=[_job_out(job, now=now) for job in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=JobOut)
def read_job(job_id: UUID, session: Session = Depends(get_db)) -> JobOut:
    """Return one job, or 404 when it was never discovered."""
    job = jobs_service.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} does not exist")
    return _job_out(job)


@router.post("/{job_id}/verify", response_model=JobVerificationOut)
def verify_job(
    job_id: UUID,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    firecrawl: FirecrawlClient = Depends(get_firecrawl),
) -> JobVerificationOut:
    """Re-check one saved job against its own source URL.

    The job is updated in place — a shared row, not a private copy — and the
    committed result is returned with the whole job. A failed check returns a
    ``check_failed`` outcome and preserves the previous availability and
    verification timestamp; nothing here ever auto-closes on a 404, timeout,
    5xx, access denial or empty page. Repeated checks within the configured
    cooldown are a 429.
    """
    job = jobs_service.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} does not exist")
    settings = get_settings()
    result = verify_job_availability(
        session,
        job,
        firecrawl=firecrawl,
        cooldown_seconds=settings.job_verification_cooldown_seconds,
    )
    session.commit()
    return JobVerificationOut(
        job=_job_out(job),
        outcome=result.outcome,
        message=result.message,
        checked_at=result.checked_at,
    )


__all__ = ["router"]