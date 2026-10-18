"""Behaviour of the job matching endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import func, select

from app.agents.job_matching import MatchResult, PROMPT_VERSION
from app.core.config import Settings
from app.models.agent_run import AgentRun
from app.models.match import Match
from tests.api.support import (
    MATCH,
    add_analysis,
    build_candidate,
    build_candidate_with_resume,
    build_job,
    login,
)
from tests.factories import make_user
from tests.fake_clients import FakeGeminiClient


def _as_candidate(client_factory, world, gemini):
    client = client_factory(gemini=gemini)
    login(client, world.candidate.user)
    return client


def _world(session, *, with_analysis: bool = True) -> SimpleNamespace:
    job_world = build_job(session)
    person = build_candidate_with_resume(session)
    analysis = add_analysis(session, job_world.job.id) if with_analysis else None
    return SimpleNamespace(
        company=job_world.company,
        job=job_world.job,
        candidate=person.candidate,
        resume=person.resume,
        analysis=analysis,
    )


def test_match_defaults_to_the_current_candidate_and_resume(client_factory, session):
    world = _world(session)
    gemini = FakeGeminiClient(MATCH)
    client = _as_candidate(client_factory, world, gemini)

    response = client.post(f"/api/jobs/{world.job.id}/match")

    assert response.status_code == 200
    body = response.json()
    assert body["agent_name"] == "job_matching"
    assert body["status"] == "succeeded"
    assert body["candidate_id"] == str(world.candidate.id)
    assert body["output"]["job_id"] == str(world.job.id)
    assert body["output"]["candidate_id"] == str(world.candidate.id)
    assert body["output"]["resume_document_id"] == str(world.resume.id)
    assert body["output"]["job_analysis_id"] == str(world.analysis.id)
    assert body["output"]["prompt_version"] == PROMPT_VERSION
    assert body["output"]["match"]["score"] == 72.5
    assert body["output"]["match"]["verdict"] == "good_match"
    assert len(gemini.calls) == 1
    assert gemini.calls[0]["schema"] is MatchResult

    row = session.execute(select(Match)).scalars().one()
    assert row.job_id == world.job.id
    assert row.candidate_id == world.candidate.id
    assert row.resume_document_id == world.resume.id


def test_matching_is_disabled_without_gemini_or_run(
    client_factory, session, monkeypatch
):
    world = _world(session)
    gemini = FakeGeminiClient(MATCH)
    client = _as_candidate(client_factory, world, gemini)
    before_matches = session.scalar(select(func.count()).select_from(Match))
    monkeypatch.setattr(
        "app.api.deps.get_settings",
        lambda: Settings(_env_file=None, enable_resume_ai=False),
    )

    response = client.post(f"/api/jobs/{world.job.id}/match")

    assert response.status_code == 403
    assert response.json() == {"detail": "resume AI features are disabled"}
    assert gemini.calls == []
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert session.scalar(select(func.count()).select_from(Match)) == before_matches


def test_match_accepts_an_explicitly_empty_body(client_factory, session):
    world = _world(session)
    gemini = FakeGeminiClient(MATCH)
    client = _as_candidate(client_factory, world, gemini)

    response = client.post(f"/api/jobs/{world.job.id}/match", json={})

    assert response.status_code == 200
    assert response.json()["output"]["match"]["score"] == 72.5


def test_repeated_match_reuses_run_and_row(client_factory, session):
    world = _world(session)
    gemini = FakeGeminiClient(MATCH)
    client = _as_candidate(client_factory, world, gemini)

    first = client.post(f"/api/jobs/{world.job.id}/match").json()
    second = client.post(f"/api/jobs/{world.job.id}/match").json()

    assert second["id"] == first["id"]
    assert second["output"] == first["output"]
    assert len(gemini.calls) == 1
    assert session.scalar(select(func.count()).select_from(Match)) == 1
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 1


def test_match_without_a_job_analysis_is_409(client_factory, session):
    world = _world(session, with_analysis=False)
    gemini = FakeGeminiClient(MATCH)
    client = _as_candidate(client_factory, world, gemini)

    response = client.post(f"/api/jobs/{world.job.id}/match")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"].startswith("JobAnalysisNotFoundError")
    assert detail["run"]["status"] == "pending"
    assert session.scalar(select(func.count()).select_from(Match)) == 0
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 1
    assert gemini.calls == []


def test_match_without_a_candidate_is_404(client_factory, session):
    job_world = build_job(session)
    client = client_factory(gemini=FakeGeminiClient(MATCH))
    login(client, make_user(session))

    response = client.post(f"/api/jobs/{job_world.job.id}/match")

    assert response.status_code == 404
    assert "POST /api/candidates" in response.json()["detail"]
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_match_without_a_master_resume_is_409(client_factory, session):
    job_world = build_job(session)
    candidate = build_candidate(session)
    client = client_factory(gemini=FakeGeminiClient(MATCH))
    login(client, candidate.user)

    response = client.post(f"/api/jobs/{job_world.job.id}/match")

    assert response.status_code == 409
    assert "POST /api/resumes/master" in response.json()["detail"]
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_explicit_unknown_ids_are_404(client_factory, session):
    world = _world(session)
    client = client_factory(gemini=FakeGeminiClient(MATCH))
    login(client, world.candidate.user)

    unknown_candidate = client.post(
        f"/api/jobs/{world.job.id}/match", json={"candidate_id": str(uuid4())}
    )
    unknown_resume = client.post(
        f"/api/jobs/{world.job.id}/match",
        json={"resume_document_id": str(uuid4())},
    )
    unknown_job = client.post(f"/api/jobs/{uuid4()}/match")

    assert unknown_candidate.status_code == 404
    assert unknown_resume.status_code == 404
    assert unknown_job.status_code == 404
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_nil_uuid_is_rejected_as_422(client_factory, session):
    world = _world(session)
    client = client_factory(gemini=FakeGeminiClient(MATCH))
    login(client, world.candidate.user)

    response = client.post(
        f"/api/jobs/{world.job.id}/match",
        json={"candidate_id": "00000000-0000-0000-0000-000000000000"},
    )

    assert response.status_code == 422


def test_explicit_ids_are_honoured(client_factory, session):
    world = _world(session)
    gemini = FakeGeminiClient(MATCH)
    client = _as_candidate(client_factory, world, gemini)

    response = client.post(
        f"/api/jobs/{world.job.id}/match",
        json={
            "candidate_id": str(world.candidate.id),
            "resume_document_id": str(world.resume.id),
        },
    )

    assert response.status_code == 200
    output = response.json()["output"]
    assert output["candidate_id"] == str(world.candidate.id)
    assert output["resume_document_id"] == str(world.resume.id)
