"""Shared FastAPI dependencies for local application services."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.agents.runtime import HandlerRegistry
from app.clients.firecrawl import FirecrawlClient
from app.core.config import get_settings
from app.db.session import get_db
from app.models.candidate import Candidate
from app.services import candidates as candidate_service

NO_CANDIDATE_DETAIL = "the local profile could not be initialized"
NO_MASTER_RESUME_DETAIL = (
    "no master resume uploaded yet; upload one with POST /api/resumes/master"
)
RESUME_AI_DISABLED_DETAIL = "resume AI features are disabled"


def get_registry(request: Request) -> HandlerRegistry:
    """Return the handler registry this app executes agents with."""
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        raise HTTPException(status_code=500, detail="agent registry is not configured")
    return registry


def get_firecrawl(request: Request) -> FirecrawlClient:
    """Return the Firecrawl client configured for this app."""
    client = getattr(request.app.state, "firecrawl", None)
    if client is None:
        raise HTTPException(status_code=500, detail="firecrawl client is not configured")
    return client


def ensure_resume_ai_enabled() -> None:
    """Fail closed before an endpoint stores or sends resume content."""
    if not get_settings().enable_resume_ai:
        raise HTTPException(status_code=403, detail=RESUME_AI_DISABLED_DETAIL)


def get_current_candidate(session: Session = Depends(get_db)) -> Candidate:
    """Return or initialize the stable profile used by this local install."""
    try:
        candidate, changed = candidate_service.get_local_candidate(session)
    except candidate_service.LocalProfileSelectionRequired as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if candidate is None:
        raise HTTPException(status_code=404, detail=NO_CANDIDATE_DETAIL)
    if changed:
        session.commit()
    return candidate


__all__ = [
    "NO_CANDIDATE_DETAIL",
    "NO_MASTER_RESUME_DETAIL",
    "RESUME_AI_DISABLED_DETAIL",
    "ensure_resume_ai_enabled",
    "get_current_candidate",
    "get_firecrawl",
    "get_registry",
]
