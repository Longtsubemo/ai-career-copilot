"""Agent endpoints: the four product agents driven synchronously.

Each route validates what it can deterministically, resolves the
authenticated account's candidate and its own resume/match, and only then
hands the request to the Agent Runtime. Ownership is checked before a run
exists, so a cross-user id can never reach agent execution. Nothing here
retries, transitions a run or writes a result directly: the run row is the
single record of what happened, and its stored output is what a caller
reads back on success.
"""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.cover_letter import (
    COVER_LETTER_AGENT_NAME,
    CoverLetterRequest,
    cover_letter_idempotency_key,
)
from app.agents.cover_letter.prompt import PROMPT_VERSION as COVER_LETTER_PROMPT_VERSION
from app.agents.discovery import (
    DISCOVERY_AGENT_NAME,
    DiscoveryRequest,
    discovery_idempotency_key,
)
from app.agents.job_analysis import (
    JOB_ANALYSIS_AGENT_NAME,
    JobAnalysisRequest,
    job_analysis_idempotency_key,
)
from app.agents.job_analysis.prompt import PROMPT_VERSION as JOB_ANALYSIS_PROMPT_VERSION
from app.agents.job_matching import (
    JOB_MATCHING_AGENT_NAME,
    JobMatchingRequest,
    job_matching_idempotency_key,
)
from app.agents.job_matching.prompt import PROMPT_VERSION as JOB_MATCHING_PROMPT_VERSION
from app.api.agent_runner import agent_run_response, run_agent_now
from app.api.deps import (
    NO_MASTER_RESUME_DETAIL,
    ensure_resume_ai_enabled,
    get_current_candidate,
    get_current_user,
    get_registry,
)
from app.api.schemas import AgentRunOut, CoverLetterHttpRequest, MatchHttpRequest
from app.core.config import get_settings
from app.db.session import get_db
from app.models.candidate import Candidate
from app.models.document import Document
from app.models.job import Job
from app.models.match import Match
from app.models.user import User
from app.services.discovery_quota import (
    DailyProviderLimitExceeded,
    reserve_provider_attempt,
)
from app.services import resume_upload

router = APIRouter(tags=["agents"])


def _require_job(session: Session, job_id: UUID) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} does not exist")
    return job


def _resolve_candidate_id(authenticated: Candidate, explicit: UUID | None) -> UUID:
    """Return the authenticated candidate's id, rejecting a mismatched id.

    A client may echo its own candidate id for compatibility, but any other
    value is a 404 — the same answer as an unknown id, so the existence of
    another account's profile is never confirmed.
    """
    if explicit is not None and explicit != authenticated.id:
        raise HTTPException(
            status_code=404, detail=f"candidate {explicit} does not exist"
        )
    return authenticated.id


def _resolve_resume_id(
    session: Session, candidate_id: UUID, explicit: UUID | None
) -> UUID:
    """Return the resume document this request acts for.

    An explicit id must exist and be owned by the authenticated candidate;
    anything else is a 404 raised before a run is created. The document's
    kind remains the agent's precondition to enforce. Without an explicit
    id the candidate's current master resume is used, which is a 409 — an
    unmet prerequisite the user can fix by uploading one.
    """
    if explicit is not None:
        document = session.get(Document, explicit)
        if document is None or document.candidate_id != candidate_id:
            raise HTTPException(
                status_code=404, detail=f"document {explicit} does not exist"
            )
        return explicit
    document = resume_upload.current_master_resume(session, candidate_id)
    if document is None:
        raise HTTPException(status_code=409, detail=NO_MASTER_RESUME_DETAIL)
    return document.id


def _resolve_match_id(
    session: Session,
    *,
    job_id: UUID,
    candidate_id: UUID,
    resume_document_id: UUID,
    explicit: UUID | None,
) -> UUID:
    """Return the match this letter is grounded in.

    An explicit id must exist, belong to the authenticated candidate and
    name the same job; anything else is a 404 before a run is created.
    Otherwise the newest match already computed for this job, candidate and
    resume is used. With no match at all there is nothing to ground a letter
    in, so the request is a 409 pointing at the endpoint that produces one.
    """
    if explicit is not None:
        match = session.get(Match, explicit)
        if (
            match is None
            or match.candidate_id != candidate_id
            or match.job_id != job_id
        ):
            raise HTTPException(
                status_code=404, detail=f"match {explicit} does not exist"
            )
        return explicit
    stmt = (
        select(Match)
        .where(
            Match.job_id == job_id,
            Match.candidate_id == candidate_id,
            Match.resume_document_id == resume_document_id,
        )
        .order_by(Match.created_at.desc(), Match.id.desc())
        .limit(1)
    )
    match = session.execute(stmt).scalar_one_or_none()
    if match is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "no match exists for this job, candidate and resume; "
                "compute one with POST /api/jobs/"
                f"{job_id}/match first"
            ),
        )
    return match.id


@router.post("/api/jobs/discover", response_model=AgentRunOut)
def discover_jobs(
    body: DiscoveryRequest,
    session: Session = Depends(get_db),
    registry=Depends(get_registry),
    user: User = Depends(get_current_user),
) -> AgentRunOut:
    """Run authenticated, quota-limited discovery for one search request."""
    user_id = user.id
    account_key = sha256(
        f"{user_id}:{discovery_idempotency_key(body)}".encode("utf-8")
    ).hexdigest()
    settings = get_settings()
    try:
        reserve_provider_attempt(
            session,
            user_id=user_id,
            user_limit=settings.discovery_daily_user_limit,
            global_limit=settings.discovery_daily_global_limit,
        )
    except DailyProviderLimitExceeded:
        raise HTTPException(
            status_code=429, detail="daily discovery limit reached"
        ) from None
    # This commit makes the attempt durable before any search or scrape call,
    # including calls that later fail.
    session.commit()
    run = run_agent_now(
        session,
        registry=registry,
        agent_name=DISCOVERY_AGENT_NAME,
        idempotency_key=f"job_discovery:{account_key}",
        payload=body.model_dump(mode="json"),
    )
    return agent_run_response(run)


@router.post("/api/jobs/{job_id}/analyze", response_model=AgentRunOut)
def analyze_job(
    job_id: UUID,
    session: Session = Depends(get_db),
    registry=Depends(get_registry),
    _user: User = Depends(get_current_user),
) -> AgentRunOut:
    """Run job analysis for one job; repeat calls reuse the stored result."""
    _require_job(session, job_id)
    request = JobAnalysisRequest(job_id=job_id)
    run = run_agent_now(
        session,
        registry=registry,
        agent_name=JOB_ANALYSIS_AGENT_NAME,
        idempotency_key=job_analysis_idempotency_key(
            job_id, JOB_ANALYSIS_PROMPT_VERSION
        ),
        payload=request.model_dump(mode="json"),
        job_id=job_id,
    )
    return agent_run_response(run)


@router.post("/api/jobs/{job_id}/match", response_model=AgentRunOut)
def match_job(
    job_id: UUID,
    body: MatchHttpRequest | None = None,
    session: Session = Depends(get_db),
    registry=Depends(get_registry),
    candidate: Candidate = Depends(get_current_candidate),
) -> AgentRunOut:
    """Run job matching for this job against the account's own resume."""
    ensure_resume_ai_enabled()
    _require_job(session, job_id)
    candidate_id = _resolve_candidate_id(
        candidate, body.candidate_id if body else None
    )
    resume_document_id = _resolve_resume_id(
        session, candidate_id, body.resume_document_id if body else None
    )
    request = JobMatchingRequest(
        job_id=job_id,
        candidate_id=candidate_id,
        resume_document_id=resume_document_id,
    )
    run = run_agent_now(
        session,
        registry=registry,
        agent_name=JOB_MATCHING_AGENT_NAME,
        idempotency_key=job_matching_idempotency_key(
            job_id,
            candidate_id,
            resume_document_id,
            JOB_MATCHING_PROMPT_VERSION,
        ),
        payload=request.model_dump(mode="json"),
        job_id=job_id,
        candidate_id=candidate_id,
    )
    return agent_run_response(run)


@router.post("/api/jobs/{job_id}/cover-letter", response_model=AgentRunOut)
def generate_cover_letter(
    job_id: UUID,
    body: CoverLetterHttpRequest | None = None,
    session: Session = Depends(get_db),
    registry=Depends(get_registry),
    candidate: Candidate = Depends(get_current_candidate),
) -> AgentRunOut:
    """Generate a cover letter grounded in the account's own match and resume."""
    ensure_resume_ai_enabled()
    _require_job(session, job_id)
    candidate_id = _resolve_candidate_id(
        candidate, body.candidate_id if body else None
    )
    resume_document_id = _resolve_resume_id(
        session, candidate_id, body.resume_document_id if body else None
    )
    match_id = _resolve_match_id(
        session,
        job_id=job_id,
        candidate_id=candidate_id,
        resume_document_id=resume_document_id,
        explicit=body.match_id if body else None,
    )
    request = CoverLetterRequest(
        job_id=job_id,
        candidate_id=candidate_id,
        master_resume_document_id=resume_document_id,
        match_id=match_id,
    )
    run = run_agent_now(
        session,
        registry=registry,
        agent_name=COVER_LETTER_AGENT_NAME,
        idempotency_key=cover_letter_idempotency_key(
            job_id,
            candidate_id,
            resume_document_id,
            match_id,
            COVER_LETTER_PROMPT_VERSION,
        ),
        payload=request.model_dump(mode="json"),
        job_id=job_id,
        candidate_id=candidate_id,
    )
    return agent_run_response(run)


__all__ = ["router"]
