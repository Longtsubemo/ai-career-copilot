"""Durable, idempotent agent execution state."""

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agent_run import AgentRun
from app.models.enums import RunStatus
from tests.factories import make_candidate, make_job


def make_run(session: Session, key: str, status: RunStatus, **overrides: object) -> AgentRun:
    run = AgentRun(
        agent_name=overrides.pop("agent_name", "job_analysis"),
        status=status,
        idempotency_key=key,
        **overrides,
    )
    session.add(run)
    session.flush()
    return run


def test_defaults_for_attempts_and_status(session: Session) -> None:
    run = make_run(session, "default-check", RunStatus.pending)
    session.commit()

    assert run.status == RunStatus.pending
    assert run.attempt_count == 1
    assert run.max_attempts == 3
    assert run.started_at is None
    assert run.finished_at is None


def test_all_run_statuses_are_accepted(session: Session) -> None:
    for status in RunStatus:
        make_run(session, f"status-{status.value}", status)
    session.commit()


def test_unknown_run_status_is_rejected(session: Session) -> None:
    run = make_run(session, "bad-status", RunStatus.pending)
    session.commit()

    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE agent_runs SET status = 'almost_done' WHERE id = :id"),
            {"id": run.id},
        )
        session.commit()
    session.rollback()


def test_second_successful_run_for_same_key_is_rejected(session: Session) -> None:
    make_run(session, "job_analysis:abc:v1", RunStatus.succeeded)
    session.commit()

    with pytest.raises(IntegrityError):
        make_run(session, "job_analysis:abc:v1", RunStatus.succeeded)
        session.commit()
    session.rollback()


def test_failed_attempts_do_not_block_a_later_success(session: Session) -> None:
    make_run(session, "job_analysis:xyz:v1", RunStatus.failed, error="gemini timeout")
    make_run(session, "job_analysis:xyz:v1", RunStatus.failed, error="gemini timeout")
    session.commit()

    make_run(session, "job_analysis:xyz:v1", RunStatus.succeeded)
    session.commit()

    succeeded = session.execute(
        select(AgentRun).where(
            AgentRun.idempotency_key == "job_analysis:xyz:v1",
            AgentRun.status == RunStatus.succeeded,
        )
    ).scalars().all()
    assert len(succeeded) == 1


def test_pending_and_running_runs_may_share_a_key(session: Session) -> None:
    make_run(session, "job_analysis:pq:v1", RunStatus.pending)
    make_run(session, "job_analysis:pq:v1", RunStatus.running)
    make_run(session, "job_analysis:pq:v1", RunStatus.cancelled)
    session.commit()


def test_successful_runs_are_deduplicated_globally_across_agents(session: Session) -> None:
    """Idempotency keys are globally unique, so callers must namespace them."""
    make_run(session, "job_analysis:job-1:v1", RunStatus.succeeded, agent_name="job_analysis")
    session.commit()

    with pytest.raises(IntegrityError):
        make_run(
            session,
            "job_analysis:job-1:v1",
            RunStatus.succeeded,
            agent_name="cover_letter",
        )
        session.commit()
    session.rollback()


def test_namespaced_keys_do_not_collide(session: Session) -> None:
    make_run(session, "job_analysis:job-1:v1", RunStatus.succeeded, agent_name="job_analysis")
    make_run(session, "cover_letter:job-1:v1", RunStatus.succeeded, agent_name="cover_letter")
    session.commit()


def test_jsonb_payloads_round_trip(session: Session) -> None:
    run = make_run(
        session,
        "jsonb-round-trip",
        RunStatus.succeeded,
        input={"job_id": "abc", "prompt_version": "v1"},
        output={"required_skills": ["python", "sql"], "seniority": "mid"},
    )
    session.commit()
    session.expire_all()

    reloaded = session.get(AgentRun, run.id)
    assert reloaded is not None
    assert reloaded.input == {"job_id": "abc", "prompt_version": "v1"}
    assert reloaded.output == {"required_skills": ["python", "sql"], "seniority": "mid"}


def test_run_can_reference_a_job_and_candidate(session: Session) -> None:
    candidate = make_candidate(session)
    job = make_job(session)
    run = make_run(session, "with-refs", RunStatus.running, job_id=job.id, candidate_id=candidate.id)
    session.commit()
    session.expire_all()

    reloaded = session.get(AgentRun, run.id)
    assert reloaded is not None
    assert reloaded.job is not None
    assert reloaded.candidate is not None


def test_run_survives_deletion_of_its_job(session: Session) -> None:
    job = make_job(session)
    run = make_run(session, "orphan-survivor", RunStatus.succeeded, job_id=job.id)
    session.commit()
    run_id = run.id

    session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job.id})
    session.commit()
    session.expire_all()

    surviving = session.get(AgentRun, run_id)
    assert surviving is not None
    assert surviving.job_id is None


def test_skip_locked_claim_skips_rows_held_by_another_transaction(engine: Engine) -> None:
    """PostgreSQL is the work queue, so a second worker must not grab a held row."""
    keys = ["claim-a", "claim-b", "claim-c"]
    seeder = Session(engine)
    try:
        for key in keys:
            seeder.add(
                AgentRun(
                    agent_name="job_analysis",
                    status=RunStatus.pending,
                    idempotency_key=key,
                )
            )
        seeder.commit()

        claim = (
            select(AgentRun.id)
            .where(
                AgentRun.status == RunStatus.pending,
                AgentRun.idempotency_key.in_(keys),
            )
            .order_by(AgentRun.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

        with engine.connect() as conn_one, engine.connect() as conn_two:
            tx_one = conn_one.begin()
            tx_two = conn_two.begin()
            try:
                held_rows = conn_one.execute(claim).scalars().all()
                assert len(held_rows) == 1

                claimed_rows = conn_two.execute(claim).scalars().all()
                assert len(claimed_rows) == 1
                assert claimed_rows[0] != held_rows[0]
            finally:
                tx_two.rollback()
                tx_one.rollback()
    finally:
        seeder.rollback()
        seeder.execute(delete(AgentRun).where(AgentRun.idempotency_key.in_(keys)))
        seeder.commit()
        seeder.close()


def test_claim_query_excludes_non_pending_runs(session: Session) -> None:
    keys = ["claim-filter-done", "claim-filter-queued"]
    make_run(session, keys[0], RunStatus.succeeded)
    make_run(session, keys[1], RunStatus.pending)
    session.commit()

    claimable = session.execute(
        select(AgentRun)
        .where(
            AgentRun.status == RunStatus.pending,
            AgentRun.idempotency_key.in_(keys),
        )
        .with_for_update(skip_locked=True)
    ).scalars().all()
    assert {run.idempotency_key for run in claimable} == {keys[1]}