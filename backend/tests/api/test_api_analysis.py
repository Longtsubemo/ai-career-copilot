"""Behaviour of the job analysis endpoint."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select

from app.clients.gemini import GeminiError
from app.models.agent_run import AgentRun
from app.models.job_analysis import JobAnalysis
from tests.api.support import ANALYSIS_PAYLOAD, ANALYSIS_RESULT, build_job
from tests.fake_clients import FakeGeminiClient


def test_analyze_runs_and_persists(client_factory, session):
    world = build_job(session)
    gemini = FakeGeminiClient(ANALYSIS_RESULT)
    client = client_factory(gemini=gemini)

    response = client.post(f"/api/jobs/{world.job.id}/analyze")

    assert response.status_code == 200
    body = response.json()
    assert body["agent_name"] == "job_analysis"
    assert body["status"] == "succeeded"
    assert body["error"] is None
    assert body["job_id"] == str(world.job.id)
    assert body["output"] == {
        "job_id": str(world.job.id),
        "prompt_version": "job-analysis-v1",
        "model": "gemini-3.8-flash",
        "analysis": ANALYSIS_PAYLOAD,
    }
    assert len(gemini.calls) == 1
    assert gemini.calls[0]["schema"] is type(ANALYSIS_RESULT)

    row = session.execute(select(JobAnalysis)).scalars().one()
    assert row.status.value == "succeeded"
    assert row.job_id == world.job.id
    assert row.prompt_version == "job-analysis-v1"


def test_repeated_analyze_reuses_run_and_analysis(client_factory, session):
    world = build_job(session)
    gemini = FakeGeminiClient(ANALYSIS_RESULT)
    client = client_factory(gemini=gemini)

    first = client.post(f"/api/jobs/{world.job.id}/analyze").json()
    second = client.post(f"/api/jobs/{world.job.id}/analyze").json()

    assert second["id"] == first["id"]
    assert second["output"] == first["output"]
    assert second["status"] == "succeeded"
    assert len(gemini.calls) == 1
    assert session.scalar(select(func.count()).select_from(JobAnalysis)) == 1
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 1


def test_unknown_job_is_404_without_creating_a_run(client_factory, session):
    gemini = FakeGeminiClient(ANALYSIS_RESULT)
    client = client_factory(gemini=gemini)

    response = client.post(f"/api/jobs/{uuid4()}/analyze")

    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"]
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert gemini.calls == []


def test_job_without_usable_content_is_409(client_factory, session):
    world = build_job(session, description=None)
    gemini = FakeGeminiClient(ANALYSIS_RESULT)
    client = client_factory(gemini=gemini)

    response = client.post(f"/api/jobs/{world.job.id}/analyze")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"].startswith("InsufficientJobContentError")
    assert detail["run"]["status"] == "pending"
    assert "Traceback" not in detail["message"]
    assert session.scalar(select(func.count()).select_from(JobAnalysis)) == 0
    assert gemini.calls == []


def test_gemini_failure_is_503(client_factory, session):
    world = build_job(session)
    gemini = FakeGeminiClient(GeminiError("gemini is unavailable"))
    client = client_factory(gemini=gemini)

    response = client.post(f"/api/jobs/{world.job.id}/analyze")

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["message"] == "GeminiError: gemini is unavailable"
    assert detail["run"]["agent_name"] == "job_analysis"
    assert detail["run"]["status"] == "pending"
    assert session.scalar(select(func.count()).select_from(JobAnalysis)) == 0
