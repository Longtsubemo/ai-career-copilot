"""Shared builders and scripted results for the HTTP API test suite.

The API suite composes existing factories with the scripted LLM results
the agent suites already use, so a route test can set up exactly the
preconditions its scenario needs — a job without an analysis, a candidate
without a resume, a match without a letter — without re-deriving any of
the payloads.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.agents.cover_letter import CoverLetterResult
from app.agents.job_analysis import JobAnalysisResult
from app.agents.job_matching import MatchResult, MatchSignal, MatchVerdict
from app.models.document import Document
from app.models.enums import DocumentKind, RunStatus
from app.models.job_analysis import JobAnalysis
from app.models.match import Match
from tests.factories import (
    TEST_PASSWORD,
    make_candidate,
    make_company,
    make_document,
    make_job,
    make_user,
)

RESUME_TEXT = "EXPERIENCE evidence: built agent pipelines in python."

SESSION_COOKIE = "career_copilot_session"
CSRF_COOKIE = "career_copilot_csrf"
CSRF_HEADER = "X-CSRF-Token"


def _arm_csrf(client) -> None:
    """Mirror the readable CSRF cookie into the header unsafe calls need."""
    token = client.cookies.get(CSRF_COOKIE)
    if token:
        client.headers[CSRF_HEADER] = token


def login(client, user, password: str = TEST_PASSWORD):
    """Log an existing account in and arm the client's CSRF header."""
    response = client.post(
        "/api/auth/login", json={"email": user.email, "password": password}
    )
    assert response.status_code == 200, response.text
    _arm_csrf(client)
    return response


def register(
    client,
    *,
    email: str = "ada.lovelace@example.com",
    password: str = "password123",
    name: str = "Ada Lovelace",
):
    """Register an account (which creates its candidate) and arm CSRF."""
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "name": name},
    )
    assert response.status_code == 201, response.text
    _arm_csrf(client)
    return response.json()


def make_user_client(client_factory, session, *, user=None, **overrides):
    """Return an authenticated client and the account it is signed in as."""
    if user is None:
        user = make_user(session, **overrides)
    client = client_factory()
    login(client, user)
    return client, user

ANALYSIS_PAYLOAD: dict[str, object] = {
    "summary": "Backend role building agentic systems.",
    "confidence": 0.9,
    "required_skills": ["python", "postgresql"],
    "preferred_skills": ["kubernetes"],
    "responsibilities": ["design agent pipelines"],
    "qualifications": ["3+ years backend experience"],
    "experience_years_min": 3,
    "experience_years_max": 6,
    "seniority": "mid",
    "remote_type": "hybrid",
    "domain_keywords": ["artificial intelligence"],
    "technologies": ["python"],
    "education_requirements": ["bachelor's degree"],
    "certifications": [],
    "ambiguities": ["on-call expectations are unclear"],
}

ANALYSIS_RESULT = JobAnalysisResult.model_validate(ANALYSIS_PAYLOAD)

MATCH = MatchResult(
    score=72.5,
    verdict=MatchVerdict.good_match,
    matched_skills=["python", "fastapi"],
    missing_required_skills=["kubernetes"],
    missing_preferred_skills=["terraform"],
    strengths=["five years building backends"],
    gaps=["no Kubernetes experience"],
    experience_match=MatchSignal.matched,
    education_match=MatchSignal.matched,
    location_match=MatchSignal.unclear,
    seniority_match=MatchSignal.matched,
    rationale="Strong backend overlap; two preferred tools absent.",
    confidence=0.85,
)

LETTER = CoverLetterResult(
    content=(
        "Dear Hiring Manager,\n\n"
        "I am writing to apply for the Senior AI Engineer role.\n\n"
        "Over five years I have built backends and agent pipelines in python.\n\n"
        "Kind regards"
    ),
    confidence=0.9,
)


def build_job(session: Session, **overrides: object) -> SimpleNamespace:
    """A company plus one analysable job posting."""
    company = make_company(session, name=overrides.pop("company_name", "Acme Inc."))
    job = make_job(
        session,
        company=company,
        title=overrides.pop("title", "Senior AI Engineer"),
        description=overrides.pop("description", "Build agentic systems."),
        location=overrides.pop("location", "Bangalore"),
        employment_type=overrides.pop("employment_type", "Full-time"),
        experience_level=overrides.pop("experience_level", "1-3 years"),
        **overrides,
    )
    return SimpleNamespace(company=company, job=job)


def build_candidate(session: Session, **overrides: object):
    """The single candidate profile with enough evidence to match on."""
    return make_candidate(
        session,
        name=overrides.pop("name", "Ada Lovelace"),
        summary=overrides.pop("summary", "Backend specialist."),
        **overrides,
    )


def build_candidate_with_resume(
    session: Session, **overrides: object
) -> SimpleNamespace:
    """A candidate plus their current master resume."""
    candidate = build_candidate(session, **overrides)
    resume = make_document(
        session, candidate, DocumentKind.master_resume, RESUME_TEXT
    )
    return SimpleNamespace(candidate=candidate, resume=resume)


def add_analysis(
    session: Session,
    job_id,
    *,
    prompt_version: str = "job-analysis-v1",
    status: RunStatus = RunStatus.succeeded,
    payload: dict[str, object] | None = ANALYSIS_PAYLOAD,
) -> JobAnalysis:
    """Persist a succeeded job analysis the way the agent would."""
    row = JobAnalysis(
        job_id=job_id,
        prompt_version=prompt_version,
        status=status,
        analysis=payload,
        model="gemini-3.8-flash",
    )
    session.add(row)
    session.flush()
    return row


def add_match(
    session: Session,
    *,
    job,
    candidate,
    resume,
    analysis,
    result: MatchResult = MATCH,
    prompt_version: str = "job-matching-v1",
) -> Match:
    """Persist a match the way the matching agent would."""
    detail = result.model_dump(mode="json")
    score = detail.pop("score")
    row = Match(
        job_id=job.id,
        candidate_id=candidate.id,
        resume_document_id=resume.id,
        job_analysis_id=analysis.id,
        score=score,
        verdict=detail,
        model="gemini-3.8-flash",
        prompt_version=prompt_version,
    )
    session.add(row)
    session.flush()
    return row


def build_full_world(session: Session) -> SimpleNamespace:
    """Job, candidate, resume, analysis and match — everything but a letter."""
    world = build_job(session)
    person = build_candidate_with_resume(session)
    analysis = add_analysis(session, world.job.id)
    match = add_match(
        session,
        job=world.job,
        candidate=person.candidate,
        resume=person.resume,
        analysis=analysis,
    )
    return SimpleNamespace(
        company=world.company,
        job=world.job,
        candidate=person.candidate,
        resume=person.resume,
        analysis=analysis,
        match=match,
    )


__all__ = [
    "ANALYSIS_PAYLOAD",
    "ANALYSIS_RESULT",
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "LETTER",
    "MATCH",
    "RESUME_TEXT",
    "SESSION_COOKIE",
    "add_analysis",
    "add_match",
    "build_candidate",
    "build_candidate_with_resume",
    "build_full_world",
    "build_job",
    "login",
    "make_user_client",
    "register",
]
