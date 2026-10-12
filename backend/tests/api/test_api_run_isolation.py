"""An agent request may only touch the run it created or resumed.

The runtime's shared ``claim_next`` claims the oldest pending run of an agent
across every caller. The HTTP surface must not use it: a request from one
account must never claim, execute, leak or overwrite a run that belongs to a
different account. These tests seed a foreign pending run and prove an
authenticated request leaves it completely untouched.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.agents.job_matching import JOB_MATCHING_AGENT_NAME
from app.models.agent_run import AgentRun
from app.models.enums import RunStatus
from tests.api.support import (
    MATCH,
    add_analysis,
    build_candidate_with_resume,
    build_full_world,
    build_job,
    login,
)
from tests.fake_clients import FakeGeminiClient


def _seed_pending_matching_run(session, world) -> AgentRun:
    run = AgentRun(
        agent_name=JOB_MATCHING_AGENT_NAME,
        status=RunStatus.pending,
        idempotency_key=(
            f"job_matching:{world.job.id}:{world.candidate.id}:"
            f"{world.resume.id}:victim"
        ),
        input={
            "job_id": str(world.job.id),
            "candidate_id": str(world.candidate.id),
            "resume_document_id": str(world.resume.id),
        },
        candidate_id=world.candidate.id,
    )
    session.add(run)
    session.flush()
    return run


def test_a_request_never_executes_another_candidates_pending_run(
    client_factory, session
):
    victim = build_full_world(session)
    victim_run = _seed_pending_matching_run(session, victim)

    world = build_job(session, company_name="Globex")
    add_analysis(session, world.job.id)
    actor = build_candidate_with_resume(session)
    client = client_factory(gemini=FakeGeminiClient(MATCH))
    login(client, actor.candidate.user)

    response = client.post(f"/api/jobs/{world.job.id}/match")

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["id"] != str(victim_run.id)

    session.refresh(victim_run)
    assert victim_run.status == RunStatus.pending
    assert victim_run.output is None
    assert victim_run.error is None
    assert victim_run.attempt_count == 1


def test_a_request_never_returns_another_candidates_pending_output(
    client_factory, session
):
    victim = build_full_world(session)
    victim_run = _seed_pending_matching_run(session, victim)

    world = build_job(session, company_name="Globex")
    add_analysis(session, world.job.id)
    actor = build_candidate_with_resume(session)
    client = client_factory(gemini=FakeGeminiClient(MATCH))
    login(client, actor.candidate.user)

    client.post(f"/api/jobs/{world.job.id}/match")

    runs = session.execute(select(AgentRun)).scalars().all()
    assert {run.agent_name for run in runs} == {JOB_MATCHING_AGENT_NAME}
    succeeded = [run for run in runs if run.status is RunStatus.succeeded]
    assert len(succeeded) == 1
    assert succeeded[0].candidate_id == actor.candidate.id

    stored = session.get(AgentRun, victim_run.id)
    assert stored.status is RunStatus.pending
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 2
