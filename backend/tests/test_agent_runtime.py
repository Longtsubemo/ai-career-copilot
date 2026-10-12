"""End-to-end behaviour of the generic Agent Runtime.

The ``test_agent`` handlers here are deterministic runtime fixtures only;
the real Job Discovery agent is exercised in ``test_discovery_runtime.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.runtime import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    AgentRuntime,
    HandlerNotRegisteredError,
    HandlerRegistry,
    InvalidHandlerOutputError,
    InvalidRunInputError,
    InvalidRunStateError,
    InvalidTransitionError,
    RunContext,
    apply_transition,
    can_transition,
    ensure_transition,
    is_terminal,
    register_handler,
)
from app.models.agent_run import AgentRun
from app.models.enums import RunStatus
from tests.factories import make_candidate, make_job, unique_suffix

TEST_AGENT = "test_agent"


def double_handler(payload: dict | None, context: RunContext) -> dict:
    """Test fixture: doubles ``value`` (2 -> 4)."""
    return {"result": payload["value"] * 2}


@pytest.fixture
def registry() -> HandlerRegistry:
    registry = HandlerRegistry()
    registry.register(TEST_AGENT, double_handler)
    return registry


@pytest.fixture
def runtime(session: Session, registry: HandlerRegistry) -> AgentRuntime:
    return AgentRuntime(session, registry=registry)


def create_run(runtime: AgentRuntime, label: str, **overrides: object) -> AgentRun:
    agent_name = overrides.pop("agent_name", TEST_AGENT)
    key = overrides.pop("idempotency_key", f"runtime:{label}:{unique_suffix()}")
    return runtime.create_run(
        agent_name=agent_name, idempotency_key=key, **overrides
    )


def run_count(session: Session, idempotency_key: str) -> int:
    return session.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.idempotency_key == idempotency_key)
    )


ALL_TRANSITIONS = [
    (current, target) for current in RunStatus for target in RunStatus
]


@pytest.mark.parametrize(
    "current,target",
    ALL_TRANSITIONS,
    ids=[f"{c.value}->{t.value}" for c, t in ALL_TRANSITIONS],
)
def test_every_status_transition_is_decided_deterministically(
    current: RunStatus, target: RunStatus
) -> None:
    expected = (current, target) in {
        (RunStatus.pending, RunStatus.running),
        (RunStatus.pending, RunStatus.cancelled),
        (RunStatus.running, RunStatus.succeeded),
        (RunStatus.running, RunStatus.failed),
        (RunStatus.running, RunStatus.pending),
        (RunStatus.running, RunStatus.cancelled),
    }
    assert can_transition(current, target) is expected
    if expected:
        ensure_transition(current, target)
    else:
        with pytest.raises(InvalidTransitionError):
            ensure_transition(current, target)


def test_terminal_statuses_have_no_outgoing_transitions() -> None:
    assert TERMINAL_STATUSES == {
        RunStatus.succeeded,
        RunStatus.failed,
        RunStatus.cancelled,
    }
    for status in TERMINAL_STATUSES:
        assert not ALLOWED_TRANSITIONS[status]
        assert is_terminal(status)
    assert not is_terminal(RunStatus.pending)
    assert not is_terminal(RunStatus.running)


def test_apply_transition_writes_the_new_status() -> None:
    run = AgentRun(
        agent_name=TEST_AGENT,
        status=RunStatus.pending,
        idempotency_key="runtime:apply-transition",
    )
    apply_transition(run, RunStatus.running)
    assert run.status == RunStatus.running


def test_create_run_creates_a_pending_run(runtime: AgentRuntime) -> None:
    run = create_run(runtime, "create", input={"value": 2})

    assert run.status == RunStatus.pending
    assert run.agent_name == TEST_AGENT
    assert run.input == {"value": 2}
    assert run.output is None
    assert run.error is None
    assert run.attempt_count == 1
    assert run.max_attempts == 3
    assert run.started_at is None
    assert run.finished_at is None


def test_create_run_records_job_and_candidate_references(
    session: Session, runtime: AgentRuntime
) -> None:
    job = make_job(session)
    candidate = make_candidate(session)

    run = create_run(
        runtime, "refs", job_id=job.id, candidate_id=candidate.id, input={"value": 2}
    )

    assert run.job_id == job.id
    assert run.candidate_id == candidate.id


@pytest.mark.parametrize(
    "overrides",
    [
        {"agent_name": ""},
        {"agent_name": "a" * 101},
        {"idempotency_key": ""},
        {"idempotency_key": "k" * 256},
        {"max_attempts": 0},
        {"input": "not-a-mapping"},
        {"input": {"not": object()}},
    ],
)
def test_create_run_rejects_invalid_input(
    runtime: AgentRuntime, overrides: dict
) -> None:
    params: dict = {
        "agent_name": TEST_AGENT,
        "idempotency_key": f"runtime:invalid:{unique_suffix()}",
    }
    params.update(overrides)
    with pytest.raises(InvalidRunInputError):
        runtime.create_run(**params)


def test_claim_marks_the_run_running_and_sets_started_at(
    runtime: AgentRuntime,
) -> None:
    run = create_run(runtime, "claim", input={"value": 2})

    claimed = runtime.claim_next(agent_name=TEST_AGENT)

    assert claimed is run
    assert claimed.status == RunStatus.running
    assert claimed.started_at is not None
    assert claimed.finished_at is None
    assert claimed.attempt_count == 1


def test_claim_returns_none_when_no_run_is_eligible(
    runtime: AgentRuntime,
) -> None:
    assert runtime.claim_next(agent_name=TEST_AGENT) is None


def test_claim_only_returns_runs_of_the_requested_agent(
    runtime: AgentRuntime,
) -> None:
    other = create_run(runtime, "other-agent", agent_name="other_agent")
    claimed = runtime.claim_next(agent_name=TEST_AGENT)

    assert claimed is None
    assert other.status == RunStatus.pending


def test_claim_ignores_terminal_runs(runtime: AgentRuntime) -> None:
    run = create_run(runtime, "terminal", input={"value": 2})
    runtime.claim_next(agent_name=TEST_AGENT)
    runtime.execute(run)
    assert run.status == RunStatus.succeeded

    assert runtime.claim_next(agent_name=TEST_AGENT) is None


def test_claim_run_claims_only_the_named_run(runtime: AgentRuntime) -> None:
    first = create_run(runtime, "claim-run-first", input={"value": 2})
    second = create_run(runtime, "claim-run-second", input={"value": 3})

    claimed = runtime.claim_run(second.id)

    assert claimed is second
    assert claimed.status == RunStatus.running
    assert claimed.started_at is not None
    assert first.status == RunStatus.pending


def test_claim_run_returns_none_for_an_unknown_id(
    runtime: AgentRuntime,
) -> None:
    assert runtime.claim_run(uuid4()) is None


def test_claim_run_returns_none_when_the_run_is_already_claimed(
    runtime: AgentRuntime,
) -> None:
    run = create_run(runtime, "claim-run-again", input={"value": 2})
    assert runtime.claim_run(run.id) is not None

    assert runtime.claim_run(run.id) is None


def test_claim_run_returns_none_when_the_run_is_terminal(
    runtime: AgentRuntime,
) -> None:
    run = create_run(runtime, "claim-run-terminal", input={"value": 2})
    claimed = runtime.claim_run(run.id)
    runtime.execute(claimed)

    assert run.status == RunStatus.succeeded
    assert runtime.claim_run(run.id) is None


def test_claim_run_increments_attempt_count_on_a_retry(
    runtime: AgentRuntime, registry: HandlerRegistry
) -> None:
    def explode(payload: dict | None, context: RunContext) -> dict:
        raise RuntimeError("boom")

    registry.register("retry_agent", explode)
    run = create_run(runtime, "claim-run-retry", agent_name="retry_agent")

    first = runtime.claim_run(run.id)
    assert first.attempt_count == 1
    runtime.execute(first)
    assert run.status == RunStatus.pending

    second = runtime.claim_run(run.id)
    assert second is run
    assert second.attempt_count == 2
    assert second.started_at is not None


def test_successful_handler_marks_the_run_succeeded(
    runtime: AgentRuntime,
) -> None:
    run = create_run(runtime, "success", input={"value": 2})
    claimed = runtime.claim_next(agent_name=TEST_AGENT)

    runtime.execute(claimed)

    assert claimed.status == RunStatus.succeeded
    assert claimed.output == {"result": 4}
    assert claimed.error is None
    assert claimed.input == {"value": 2}
    assert claimed.started_at is not None
    assert claimed.finished_at is not None
    assert claimed.attempt_count == 1


def test_run_next_claims_and_executes_in_one_step(
    runtime: AgentRuntime,
) -> None:
    run = create_run(runtime, "one-step", input={"value": 2})

    result = runtime.run_next(agent_name=TEST_AGENT)

    assert result is run
    assert result.status == RunStatus.succeeded
    assert result.output == {"result": 4}


def test_run_next_returns_none_when_the_queue_is_idle(
    runtime: AgentRuntime,
) -> None:
    assert runtime.run_next(agent_name=TEST_AGENT) is None


def test_handler_receives_input_and_runtime_context(
    session: Session, runtime: AgentRuntime, registry: HandlerRegistry
) -> None:
    seen: dict = {}

    def spy(payload: dict | None, context: RunContext) -> dict:
        seen["payload"] = payload
        seen["attempt"] = context.attempt
        seen["agent_name"] = context.agent_name
        seen["run_id"] = context.run.id
        seen["session"] = context.session
        return {"ok": True}

    registry.register("spy_agent", spy)
    run = create_run(runtime, "spy", agent_name="spy_agent", input={"value": 2})
    claimed = runtime.claim_next(agent_name="spy_agent")

    runtime.execute(claimed)

    assert seen["payload"] == {"value": 2}
    assert seen["attempt"] == 1
    assert seen["agent_name"] == "spy_agent"
    assert seen["run_id"] == run.id
    assert seen["session"] is session
    assert claimed.output == {"ok": True}


def test_handler_exception_is_persisted_and_the_run_retries(
    runtime: AgentRuntime, registry: HandlerRegistry
) -> None:
    def explode(payload: dict | None, context: RunContext) -> dict:
        raise RuntimeError("handler exploded")

    registry.register("exploding_agent", explode)
    run = create_run(runtime, "explode", agent_name="exploding_agent")
    claimed = runtime.claim_next(agent_name="exploding_agent")

    runtime.execute(claimed)

    assert claimed.status == RunStatus.pending
    assert claimed.error is not None
    assert "RuntimeError: handler exploded" in claimed.error
    assert "Traceback" in claimed.error
    assert claimed.output is None
    assert claimed.finished_at is None


def test_handler_exception_fails_the_run_when_attempts_are_exhausted(
    runtime: AgentRuntime, registry: HandlerRegistry
) -> None:
    def explode(payload: dict | None, context: RunContext) -> dict:
        raise RuntimeError("handler exploded")

    registry.register("exploding_agent", explode)
    run = create_run(
        runtime, "explode-once", agent_name="exploding_agent", max_attempts=1
    )
    claimed = runtime.claim_next(agent_name="exploding_agent")

    runtime.execute(claimed)

    assert claimed.status == RunStatus.failed
    assert "RuntimeError: handler exploded" in claimed.error
    assert claimed.finished_at is not None


def test_unregistered_agent_records_an_error_and_retries(
    runtime: AgentRuntime,
) -> None:
    run = create_run(runtime, "ghost", agent_name="ghost_agent")
    claimed = runtime.claim_next(agent_name="ghost_agent")

    runtime.execute(claimed)

    assert claimed.status == RunStatus.pending
    assert HandlerNotRegisteredError.__name__ in claimed.error
    assert "ghost_agent" in claimed.error


def test_failures_retry_until_attempts_are_exhausted(
    session: Session, runtime: AgentRuntime, registry: HandlerRegistry
) -> None:
    attempts_seen: list[int] = []

    def always_fails(payload: dict | None, context: RunContext) -> dict:
        attempts_seen.append(context.attempt)
        raise RuntimeError("boom")

    registry.register("flaky_agent", always_fails)
    run = create_run(runtime, "flaky", agent_name="flaky_agent", max_attempts=3)

    statuses = []
    for _ in range(5):
        claimed = runtime.claim_next(agent_name="flaky_agent")
        if claimed is None:
            break
        first_started_at = run.started_at
        runtime.execute(claimed)
        statuses.append(claimed.status)
        if claimed.status == RunStatus.pending:
            assert claimed.started_at == first_started_at

    assert statuses == [RunStatus.pending, RunStatus.pending, RunStatus.failed]
    assert attempts_seen == [1, 2, 3]
    assert run.attempt_count == 3
    assert run.max_attempts == 3
    assert "RuntimeError: boom" in run.error
    assert run.finished_at is not None
    assert run_count(session, run.idempotency_key) == 1


def test_retry_does_not_create_a_second_agent_run(
    session: Session, runtime: AgentRuntime, registry: HandlerRegistry
) -> None:
    def explode(payload: dict | None, context: RunContext) -> dict:
        raise RuntimeError("boom")

    registry.register("exploding_agent", explode)
    run = create_run(
        runtime, "single-row", agent_name="exploding_agent", max_attempts=2
    )

    for _ in range(2):
        claimed = runtime.claim_next(agent_name="exploding_agent")
        runtime.execute(claimed)

    assert run.status == RunStatus.failed
    assert run.attempt_count == 2
    assert run_count(session, run.idempotency_key) == 1


def test_runtime_level_max_attempts_is_configurable(
    session: Session, registry: HandlerRegistry
) -> None:
    def explode(payload: dict | None, context: RunContext) -> dict:
        raise RuntimeError("boom")

    registry.register("exploding_agent", explode)
    runtime = AgentRuntime(session, registry=registry, default_max_attempts=1)
    run = create_run(runtime, "one-shot", agent_name="exploding_agent")

    assert run.max_attempts == 1
    claimed = runtime.claim_next(agent_name="exploding_agent")
    runtime.execute(claimed)

    assert claimed.status == RunStatus.failed
    assert claimed.attempt_count == 1


def test_success_after_retry_clears_the_recorded_error(
    runtime: AgentRuntime, registry: HandlerRegistry
) -> None:
    should_fail = {"value": True}

    def flaky(payload: dict | None, context: RunContext) -> dict:
        if should_fail["value"]:
            should_fail["value"] = False
            raise RuntimeError("transient")
        return {"result": 4}

    registry.register("flaky_agent", flaky)
    run = create_run(runtime, "flaky-once", agent_name="flaky_agent")

    runtime.claim_next(agent_name="flaky_agent")
    runtime.execute(run)
    assert run.status == RunStatus.pending
    assert run.error is not None

    runtime.claim_next(agent_name="flaky_agent")
    runtime.execute(run)

    assert run.status == RunStatus.succeeded
    assert run.error is None
    assert run.output == {"result": 4}
    assert run.attempt_count == 2


def test_execute_requires_a_claimed_run(
    runtime: AgentRuntime,
) -> None:
    run = create_run(runtime, "unclaimed", input={"value": 2})
    with pytest.raises(InvalidRunStateError):
        runtime.execute(run)

    claimed = runtime.claim_next(agent_name=TEST_AGENT)
    runtime.execute(claimed)
    assert claimed.output == {"result": 4}

    with pytest.raises(InvalidRunStateError):
        runtime.execute(claimed)
    assert claimed.output == {"result": 4}


def test_non_mapping_handler_output_fails_the_run(
    runtime: AgentRuntime, registry: HandlerRegistry
) -> None:
    def weird(payload: dict | None, context: RunContext) -> dict:
        return "not a mapping"

    registry.register("weird_agent", weird)
    run = create_run(runtime, "weird", agent_name="weird_agent", max_attempts=1)
    claimed = runtime.claim_next(agent_name="weird_agent")

    runtime.execute(claimed)

    assert claimed.status == RunStatus.failed
    assert InvalidHandlerOutputError.__name__ in claimed.error
    assert "must be a mapping" in claimed.error


def test_non_serializable_handler_output_fails_the_run(
    runtime: AgentRuntime, registry: HandlerRegistry
) -> None:
    def weird(payload: dict | None, context: RunContext) -> dict:
        return {"when": object()}

    registry.register("weird_agent", weird)
    run = create_run(runtime, "non-json", agent_name="weird_agent", max_attempts=1)
    claimed = runtime.claim_next(agent_name="weird_agent")

    runtime.execute(claimed)

    assert claimed.status == RunStatus.failed
    assert "not JSON-compatible" in claimed.error


def test_duplicate_create_returns_the_existing_pending_run(
    session: Session, runtime: AgentRuntime
) -> None:
    run = create_run(runtime, "dup-pending", input={"value": 2})

    again = runtime.create_run(
        agent_name=TEST_AGENT,
        idempotency_key=run.idempotency_key,
        input={"value": 99},
    )

    assert again.id == run.id
    assert again.input == {"value": 2}
    assert run_count(session, run.idempotency_key) == 1


def test_duplicate_create_returns_the_running_run(
    session: Session, runtime: AgentRuntime
) -> None:
    run = create_run(runtime, "dup-running", input={"value": 2})
    claimed = runtime.claim_next(agent_name=TEST_AGENT)

    again = runtime.create_run(
        agent_name=TEST_AGENT, idempotency_key=run.idempotency_key
    )

    assert again.id == claimed.id
    assert again.status == RunStatus.running
    assert run_count(session, run.idempotency_key) == 1


def test_create_after_success_returns_the_succeeded_run(
    session: Session, runtime: AgentRuntime
) -> None:
    run = create_run(runtime, "dup-success", input={"value": 2})
    runtime.claim_next(agent_name=TEST_AGENT)
    runtime.execute(run)
    assert run.status == RunStatus.succeeded

    again = runtime.create_run(
        agent_name=TEST_AGENT, idempotency_key=run.idempotency_key
    )

    assert again.id == run.id
    assert again.status == RunStatus.succeeded
    assert run_count(session, run.idempotency_key) == 1


def test_create_after_failure_starts_a_fresh_run(
    session: Session, runtime: AgentRuntime, registry: HandlerRegistry
) -> None:
    def explode(payload: dict | None, context: RunContext) -> dict:
        raise RuntimeError("boom")

    registry.register("exploding_agent", explode)
    first = create_run(
        runtime, "after-failure", agent_name="exploding_agent", max_attempts=1
    )
    runtime.claim_next(agent_name="exploding_agent")
    runtime.execute(first)
    assert first.status == RunStatus.failed

    second = runtime.create_run(
        agent_name="exploding_agent", idempotency_key=first.idempotency_key
    )

    assert second.id != first.id
    assert second.status == RunStatus.pending
    assert second.attempt_count == 1
    assert second.error is None
    assert run_count(session, first.idempotency_key) == 2


def test_create_after_cancel_starts_a_fresh_run(
    session: Session, runtime: AgentRuntime
) -> None:
    first = create_run(runtime, "after-cancel", input={"value": 2})
    runtime.cancel(first)
    assert first.status == RunStatus.cancelled

    second = runtime.create_run(
        agent_name=TEST_AGENT, idempotency_key=first.idempotency_key
    )

    assert second.id != first.id
    assert second.status == RunStatus.pending
    assert run_count(session, first.idempotency_key) == 2


def test_duplicate_success_is_recorded_as_a_failure(
    session: Session, runtime: AgentRuntime
) -> None:
    run = create_run(runtime, "conflict", input={"value": 2})
    runtime.claim_next(agent_name=TEST_AGENT)
    runtime.execute(run)
    assert run.status == RunStatus.succeeded

    duplicate = AgentRun(
        agent_name=TEST_AGENT,
        status=RunStatus.pending,
        idempotency_key=run.idempotency_key,
        input={"value": 2},
    )
    session.add(duplicate)
    session.flush()

    claimed = runtime.claim_next(agent_name=TEST_AGENT)
    assert claimed is duplicate
    runtime.execute(claimed)

    assert claimed.status == RunStatus.failed
    assert "DuplicateSuccessError" in claimed.error
    assert "already exists for idempotency key" in claimed.error
    assert claimed.finished_at is not None
    session.refresh(run)
    assert run.status == RunStatus.succeeded
    assert run.output == {"result": 4}
    assert run_count(session, run.idempotency_key) == 2


def test_cancel_pending_run(runtime: AgentRuntime) -> None:
    run = create_run(runtime, "cancel-pending")

    runtime.cancel(run)

    assert run.status == RunStatus.cancelled
    assert run.finished_at is not None
    assert runtime.claim_next(agent_name=TEST_AGENT) is None


def test_cancel_running_run(runtime: AgentRuntime) -> None:
    run = create_run(runtime, "cancel-running")
    runtime.claim_next(agent_name=TEST_AGENT)

    runtime.cancel(run)

    assert run.status == RunStatus.cancelled
    assert run.finished_at is not None


def test_cancel_succeeded_run_is_rejected(runtime: AgentRuntime) -> None:
    run = create_run(runtime, "cancel-succeeded", input={"value": 2})
    runtime.claim_next(agent_name=TEST_AGENT)
    runtime.execute(run)
    assert run.status == RunStatus.succeeded

    with pytest.raises(InvalidTransitionError):
        runtime.cancel(run)

    assert run.status == RunStatus.succeeded
    assert run.output == {"result": 4}


def test_cancelled_run_cannot_be_completed(runtime: AgentRuntime) -> None:
    run = create_run(runtime, "cancel-then-complete", input={"value": 2})
    runtime.claim_next(agent_name=TEST_AGENT)
    runtime.cancel(run)

    with pytest.raises(InvalidTransitionError):
        runtime.complete(run, {"result": 4})

    assert run.status == RunStatus.cancelled
    assert run.output is None


def test_complete_requires_a_running_run(runtime: AgentRuntime) -> None:
    run = create_run(runtime, "complete-pending", input={"value": 2})

    with pytest.raises(InvalidTransitionError):
        runtime.complete(run, {"result": 4})

    assert run.status == RunStatus.pending


def test_register_handler_uses_the_default_registry(
    session: Session,
) -> None:
    @register_handler("decorated_test_agent")
    def handler(payload: dict | None, context: RunContext) -> dict:
        return {"value": payload["value"]}

    runtime = AgentRuntime(session)
    run = runtime.create_run(
        agent_name="decorated_test_agent",
        idempotency_key=f"runtime:decorated:{unique_suffix()}",
        input={"value": 7},
    )
    claimed = runtime.claim_next(agent_name="decorated_test_agent")

    runtime.execute(claimed)

    assert claimed is run
    assert claimed.status == RunStatus.succeeded
    assert claimed.output == {"value": 7}


def test_registry_replaces_handlers_for_the_same_name() -> None:
    registry = HandlerRegistry()
    registry.register(TEST_AGENT, double_handler)
    assert registry.names() == {TEST_AGENT}

    def replacement(payload: dict | None, context: RunContext) -> dict:
        return {"replaced": True}

    registry.register(TEST_AGENT, replacement)
    assert registry.get(TEST_AGENT) is replacement
    assert registry.get("missing_agent") is None

    with pytest.raises(ValueError):
        registry.register("")
