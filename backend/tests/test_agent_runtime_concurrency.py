"""Concurrency guarantees of the Agent Runtime.

These tests use real PostgreSQL transactions on separate connections: the
claim path relies on ``SELECT ... FOR UPDATE SKIP LOCKED`` and idempotent
creation relies on a transaction-scoped advisory lock, and neither behaviour
can be observed inside a single session.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.agents.runtime import (
    AgentRuntime,
    HandlerRegistry,
    InvalidTransitionError,
    RunContext,
)
from app.models.agent_run import AgentRun
from app.models.enums import RunStatus
from tests.factories import unique_suffix


def double_handler(payload: dict | None, context: RunContext) -> dict:
    """Test fixture: doubles ``value`` (2 -> 4)."""
    return {"result": payload["value"] * 2}


def seed_pending_run(engine: Engine, agent_name: str, idempotency_key: str) -> UUID:
    """Commit a pending run outside the runtime, returning its id."""
    session = Session(engine)
    try:
        run = AgentRun(
            agent_name=agent_name,
            status=RunStatus.pending,
            idempotency_key=idempotency_key,
            input={"value": 2},
        )
        session.add(run)
        session.commit()
        return run.id
    finally:
        session.close()


def delete_runs(engine: Engine, idempotency_keys: list[str]) -> None:
    """Remove committed rows so later tests never claim them."""
    session = Session(engine)
    try:
        session.execute(
            delete(AgentRun).where(AgentRun.idempotency_key.in_(idempotency_keys))
        )
        session.commit()
    finally:
        session.close()


def count_runs(engine: Engine, idempotency_key: str) -> int:
    session = Session(engine)
    try:
        return session.scalar(
            select(func.count())
            .select_from(AgentRun)
            .where(AgentRun.idempotency_key == idempotency_key)
        )
    finally:
        session.close()


def test_one_pending_run_is_never_claimed_by_two_workers(engine: Engine) -> None:
    agent_name = f"claim_probe_{unique_suffix()}"
    key = f"runtime:claim:{unique_suffix()}"
    run_id = seed_pending_run(engine, agent_name, key)
    worker_one = Session(engine)
    worker_two = Session(engine)
    try:
        claimed_one = AgentRuntime(worker_one).claim_next(agent_name=agent_name)
        assert claimed_one is not None
        assert claimed_one.id == run_id
        assert claimed_one.status == RunStatus.running

        claimed_two = AgentRuntime(worker_two).claim_next(agent_name=agent_name)
        assert claimed_two is None

        worker_one.commit()

        claimed_two = AgentRuntime(worker_two).claim_next(agent_name=agent_name)
        assert claimed_two is None
        worker_two.rollback()
    finally:
        worker_one.close()
        worker_two.close()
        delete_runs(engine, [key])


def test_concurrent_workers_claim_distinct_runs(engine: Engine) -> None:
    agent_name = f"claim_probe_{unique_suffix()}"
    keys = [f"runtime:claim:{unique_suffix()}" for _ in range(3)]
    seeded = [seed_pending_run(engine, agent_name, key) for key in keys]
    worker_one = Session(engine)
    worker_two = Session(engine)
    worker_three = Session(engine)
    try:
        first = AgentRuntime(worker_one).claim_next(agent_name=agent_name)
        second = AgentRuntime(worker_two).claim_next(agent_name=agent_name)
        assert first is not None
        assert second is not None
        first_id, second_id = first.id, second.id
        assert first_id != second_id
        assert {first_id, second_id} <= set(seeded)
        worker_one.commit()
        worker_two.commit()

        third = AgentRuntime(worker_three).claim_next(agent_name=agent_name)
        assert third is not None
        assert third.id not in {first_id, second_id}
        worker_three.commit()
    finally:
        worker_one.close()
        worker_two.close()
        worker_three.close()
        delete_runs(engine, keys)


def test_concurrent_duplicate_creation_collapses_to_one_run(
    engine: Engine,
) -> None:
    key = f"runtime:create:{unique_suffix()}"
    agent_name = f"create_probe_{unique_suffix()}"
    first_created = Event()
    second_attempted = Event()
    second_finished = Event()

    def create_first() -> UUID:
        session = Session(engine)
        try:
            run = AgentRuntime(session).create_run(
                agent_name=agent_name, idempotency_key=key, input={"value": 2}
            )
            first_created.set()
            assert second_attempted.wait(timeout=10)
            blocked = not second_finished.wait(timeout=0.5)
            session.commit()
            assert blocked, (
                "the second worker must block on the idempotency advisory lock"
            )
            return run.id
        finally:
            session.close()

    def create_second() -> UUID:
        assert first_created.wait(timeout=10)
        session = Session(engine)
        try:
            second_attempted.set()
            run = AgentRuntime(session).create_run(
                agent_name=agent_name, idempotency_key=key, input={"value": 2}
            )
            second_finished.set()
            session.commit()
            return run.id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(create_first)
        second_future = executor.submit(create_second)
        first_id = first_future.result(timeout=30)
        second_id = second_future.result(timeout=30)

    try:
        assert first_id == second_id
        assert count_runs(engine, key) == 1
    finally:
        delete_runs(engine, [key])


def test_completion_cannot_overwrite_a_concurrent_cancellation(
    engine: Engine,
) -> None:
    agent_name = f"cancel_probe_{unique_suffix()}"
    key = f"runtime:cancel:{unique_suffix()}"
    registry = HandlerRegistry()
    registry.register(agent_name, double_handler)
    run_id = seed_pending_run(engine, agent_name, key)
    worker_one = Session(engine)
    worker_two = Session(engine)
    try:
        runtime_one = AgentRuntime(worker_one, registry=registry)
        claimed = runtime_one.claim_next(agent_name=agent_name)
        assert claimed is not None
        worker_one.commit()
        worker_one.refresh(claimed)
        assert claimed.status == RunStatus.running

        runtime_two = AgentRuntime(worker_two, registry=registry)
        to_cancel = worker_two.get(AgentRun, run_id)
        assert to_cancel is not None
        runtime_two.cancel(to_cancel)
        worker_two.commit()

        with pytest.raises(InvalidTransitionError):
            runtime_one.execute(claimed)
        worker_one.rollback()

        reloaded = worker_two.get(AgentRun, run_id)
        assert reloaded is not None
        assert reloaded.status == RunStatus.cancelled
        assert reloaded.output is None
    finally:
        worker_one.close()
        worker_two.close()
        delete_runs(engine, [key])
