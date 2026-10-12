"""Small object builders shared by the test modules."""

from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.candidate import Candidate
from app.models.company import Company
from app.models.document import Document
from app.models.enums import ApplicationStatus, CrawlStatus, DocumentKind
from app.models.job import Job
from app.models.user import User
from app.services import auth as auth_service
from app.services.normalization import canonicalize_url, normalize_company_name

TEST_PASSWORD = "correct-horse-battery-staple"

_counter = 0


def unique_suffix() -> str:
    """Return a token that keeps builders from colliding on unique columns."""
    global _counter
    _counter += 1
    return f"{_counter}"


def make_user(session: Session, **overrides: object) -> User:
    """Create an account with the known test password and a unique email."""
    suffix = unique_suffix()
    user = User(
        email=overrides.pop("email", f"user{suffix}@example.com"),
        password_hash=auth_service.hash_password(
            overrides.pop("password", TEST_PASSWORD)
        ),
        **overrides,
    )
    session.add(user)
    session.flush()
    return user


def make_candidate(session: Session, **overrides: object) -> Candidate:
    suffix = unique_suffix()
    user = overrides.pop("user", None)
    user_id = overrides.pop("user_id", None)
    if user_id is None:
        if user is None:
            user = make_user(session)
        user_id = user.id
    candidate = Candidate(
        name=overrides.pop("name", f"Candidate {suffix}"),
        headline=overrides.pop("headline", "AI Engineer"),
        location=overrides.pop("location", "Remote"),
        years_of_experience=overrides.pop("years_of_experience", 5.0),
        seniority=overrides.pop("seniority", "mid"),
        skills=overrides.pop("skills", ["python", "fastapi"]),
        user_id=user_id,
        **overrides,
    )
    session.add(candidate)
    session.flush()
    return candidate


def make_company(session: Session, name: str | None = None, **overrides: object) -> Company:
    display_name = name or f"Company {unique_suffix()}"
    company = Company(
        name=display_name,
        normalized_name=overrides.pop("normalized_name", normalize_company_name(display_name)),
        **overrides,
    )
    session.add(company)
    session.flush()
    return company


def make_job(session: Session, company: Company | None = None, **overrides: object) -> Job:
    suffix = unique_suffix()
    raw_url = overrides.pop("job_url", f"https://example.com/jobs/{suffix}")
    job = Job(
        company_id=overrides.pop("company_id", company.id if company else None),
        title=overrides.pop("title", "AI Engineer"),
        description=overrides.pop("description", "Build agentic systems."),
        job_url=raw_url,
        canonical_url=overrides.pop("canonical_url", canonicalize_url(raw_url)),
        source=overrides.pop("source", "searxng"),
        **overrides,
    )
    session.add(job)
    session.flush()
    return job


def make_master_resume(session: Session, candidate: Candidate, **overrides: object) -> Document:
    document = Document(
        kind=overrides.pop("kind", DocumentKind.master_resume),
        candidate_id=candidate.id,
        content=overrides.pop("content", "Master resume content"),
        **overrides,
    )
    session.add(document)
    session.flush()
    return document


def make_document(
    session: Session,
    candidate: Candidate,
    kind: DocumentKind,
    content: str,
    **overrides: object,
) -> Document:
    document = Document(
        kind=kind,
        candidate_id=candidate.id,
        content=content,
        **overrides,
    )
    session.add(document)
    session.flush()
    return document


def scraped_job(session: Session, company: Company | None = None, **overrides: object) -> Job:
    """A job that has completed its crawl."""
    return make_job(
        session,
        company,
        crawl_status=CrawlStatus.scraped,
        **overrides,
    )


def make_application_to_job(
    session: Session,
    company: Company,
    **overrides: object,
) -> Application:
    """An application carrying the snapshots a historical record needs."""
    job = make_job(session, company)
    candidate = overrides.pop("candidate", None)
    candidate_id = overrides.pop("candidate_id", None)
    if candidate_id is None:
        if candidate is None:
            candidate = make_candidate(session)
        candidate_id = candidate.id
    application = Application(
        candidate_id=candidate_id,
        job_id=overrides.pop("job_id", job.id),
        status=overrides.pop("status", ApplicationStatus.draft),
        job_title_snapshot=overrides.pop("title_snapshot", job.title),
        company_name_snapshot=overrides.pop("company_name_snapshot", company.name),
        **overrides,
    )
    session.add(application)
    session.flush()
    return application