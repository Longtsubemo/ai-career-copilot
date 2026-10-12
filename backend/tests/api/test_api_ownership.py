"""Cross-user ownership guarantees of the private HTTP surface.

Two accounts each with their own candidate, resume, match and letter must never
reach each other's data. The routes resolve the actor from the session, so a
request that names another account's id is answered exactly like an unknown id.
A forged but self-consistent CSRF pair rides no existing session.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.agent_run import AgentRun
from app.models.application import Application
from app.models.document import Document
from app.models.enums import DocumentKind
from app.models.match import Match
from tests.api.support import (
    CSRF_COOKIE,
    CSRF_HEADER,
    LETTER,
    MATCH,
    add_analysis,
    build_candidate,
    build_candidate_with_resume,
    build_full_world,
    build_job,
    login,
    register,
)
from tests.factories import make_user
from tests.fake_clients import FakeGeminiClient


def _run_count(session) -> int:
    return session.scalar(select(func.count()).select_from(AgentRun)) or 0


def test_a_user_only_reads_their_own_candidate(client_factory, session):
    first = build_candidate(session, name="Ada")
    second = build_candidate(session, name="Grace")
    client_a = client_factory()
    client_b = client_factory()
    login(client_a, first.user)
    login(client_b, second.user)

    assert client_a.get("/api/candidates/me").json()["id"] == str(first.id)
    assert client_b.get("/api/candidates/me").json()["id"] == str(second.id)
    assert [row["id"] for row in client_a.get("/api/candidates").json()] == [
        str(first.id)
    ]
    assert [row["id"] for row in client_b.get("/api/candidates").json()] == [
        str(second.id)
    ]


def test_users_only_see_their_own_master_resume(client_factory, session):
    first = build_candidate_with_resume(session)
    second = build_candidate_with_resume(session)
    client_a = client_factory()
    client_b = client_factory()
    login(client_a, first.candidate.user)
    login(client_b, second.candidate.user)

    assert client_a.get("/api/resumes/master").json()["id"] == str(first.resume.id)
    assert client_b.get("/api/resumes/master").json()["id"] == str(second.resume.id)
    assert client_a.get("/api/resumes/master/history").json()["count"] == 1
    assert client_b.get("/api/resumes/master/history").json()["count"] == 1


def test_matching_cannot_target_another_users_candidate(client_factory, session):
    job_world = build_job(session)
    add_analysis(session, job_world.job.id)
    victim = build_candidate_with_resume(session)
    actor = build_candidate_with_resume(session)
    client = client_factory(gemini=FakeGeminiClient(MATCH))
    login(client, actor.candidate.user)

    foreign_candidate = client.post(
        f"/api/jobs/{job_world.job.id}/match",
        json={"candidate_id": str(victim.candidate.id)},
    )
    foreign_resume = client.post(
        f"/api/jobs/{job_world.job.id}/match",
        json={"resume_document_id": str(victim.resume.id)},
    )

    assert foreign_candidate.status_code == 404
    assert foreign_resume.status_code == 404
    assert _run_count(session) == 0
    assert session.scalar(select(func.count()).select_from(Match)) == 0


def test_cover_letter_cannot_use_another_users_match(client_factory, session):
    victim = build_full_world(session)
    actor = build_candidate_with_resume(session)
    client = client_factory(gemini=FakeGeminiClient(LETTER))
    login(client, actor.candidate.user)

    response = client.post(
        f"/api/jobs/{victim.job.id}/cover-letter",
        json={"match_id": str(victim.match.id)},
    )

    assert response.status_code == 404
    assert _run_count(session) == 0
    letters = session.scalar(
        select(func.count())
        .select_from(Document)
        .where(Document.kind == DocumentKind.cover_letter)
    )
    assert letters == 0


def test_match_requires_authentication(client_factory, session):
    job_world = build_job(session)
    build_candidate_with_resume(session)
    add_analysis(session, job_world.job.id)
    client = client_factory(gemini=FakeGeminiClient(MATCH))

    response = client.post(f"/api/jobs/{job_world.job.id}/match")

    assert response.status_code == 401
    assert _run_count(session) == 0


def test_a_forged_but_self_consistent_csrf_pair_is_rejected(client):
    register(client)
    forged = "forged-but-consistent"
    client.cookies.set(CSRF_COOKIE, forged, domain="testserver", path="/")
    client.headers[CSRF_HEADER] = forged

    response = client.post("/api/candidates", json={"name": "Mallory"})

    assert response.status_code == 403


def test_a_genuine_csrf_pair_authorises_the_request(client):
    register(client)

    response = client.post("/api/candidates", json={"name": "Ada Again"})

    assert response.status_code == 409


def test_a_second_account_does_not_inherit_the_firsts_match(client_factory, session):
    victim = build_full_world(session)
    actor = build_candidate_with_resume(session)
    client = client_factory(gemini=FakeGeminiClient(LETTER))
    login(client, actor.candidate.user)

    response = client.post(f"/api/jobs/{victim.job.id}/cover-letter")

    assert response.status_code == 409
    assert "/match first" in response.json()["detail"]
    assert _run_count(session) == 0


def test_applications_are_scoped_to_their_owner(client_factory, session):
    first = build_candidate(session, name="Ada")
    second = build_candidate(session, name="Grace")
    client_a = client_factory()
    client_b = client_factory()
    login(client_a, first.user)
    login(client_b, second.user)

    created = client_a.post("/api/applications", json={"notes": "ada only"})
    assert created.status_code == 201
    application_id = created.json()["id"]

    assert client_a.get("/api/applications").json()["total"] == 1
    assert client_b.get("/api/applications").json()["total"] == 0
    assert client_a.get(f"/api/applications/{application_id}").status_code == 200

    assert client_b.get(f"/api/applications/{application_id}").status_code == 404
    assert (
        client_b.patch(
            f"/api/applications/{application_id}", json={"status": "applied"}
        ).status_code
        == 404
    )

    unchanged = client_a.get(f"/api/applications/{application_id}").json()
    assert unchanged["notes"] == "ada only"
    assert unchanged["status"] == "draft"


def test_an_application_records_the_callers_owner(client_factory, session):
    first = build_candidate(session, name="Ada")
    second = build_candidate(session, name="Grace")
    client_a = client_factory()
    client_b = client_factory()
    login(client_a, first.user)
    login(client_b, second.user)

    assert client_a.post("/api/applications", json={}).status_code == 201
    assert client_b.post("/api/applications", json={}).status_code == 201

    rows = session.execute(select(Application)).scalars().all()
    owners = {row.candidate_id for row in rows}
    assert owners == {first.id, second.id}
