"""Local requests always use the one explicitly selected profile."""

from sqlalchemy import func, select

from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.candidate import Candidate
from app.models.enums import DocumentKind
from app.models.document import Document
from app.models.match import Match
from tests.api.support import MATCH, add_analysis, build_candidate_with_resume, build_job
from tests.factories import make_candidate
from tests.fake_clients import FakeGeminiClient


def test_archived_profile_and_resume_cannot_be_selected_by_request(
    client_factory, session
):
    job_world = build_job(session)
    add_analysis(session, job_world.job.id)
    local = build_candidate_with_resume(session, name="Ada")
    client = client_factory(gemini=FakeGeminiClient(MATCH))
    assert client.get("/api/candidates/me").json()["id"] == str(local.candidate.id)
    archived = make_candidate(session, name="Grace")
    archived_resume = Document(
        candidate_id=archived.id,
        kind=DocumentKind.master_resume,
        content="archived resume evidence",
    )
    session.add(archived_resume)
    session.flush()

    response = client.post(
        f"/api/jobs/{job_world.job.id}/match",
        json={
            "candidate_id": str(archived.id),
            "resume_document_id": str(archived_resume.id),
        },
    )

    assert response.status_code == 404
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert session.scalar(select(func.count()).select_from(Match)) == 0


def test_profile_and_application_work_without_cookies_or_credentials(client, session):
    profile = client.get("/api/candidates/me")
    assert profile.status_code == 200
    assert not client.cookies

    application = client.post("/api/applications", json={"notes": "local only"})
    assert application.status_code == 201
    stored = session.get(Application, application.json()["id"])
    assert stored is not None
    assert str(stored.candidate_id) == profile.json()["id"]


def test_cross_site_browser_requests_are_rejected(client):
    rejected = client.patch(
        "/api/candidates/me",
        json={"headline": "forged"},
        headers={"Origin": "https://unrelated.example"},
    )
    assert rejected.status_code == 403
    assert rejected.json() == {"detail": "request origin is not allowed"}


def test_explicit_development_origin_is_allowed(client):
    client.get("/api/candidates/me")
    response = client.patch(
        "/api/candidates/me",
        json={"headline": "Local profile"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 200
