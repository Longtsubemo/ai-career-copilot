"""Synchronous execution of agent runs on behalf of HTTP routes.

A route never touches an agent directly: it hands the runtime an
idempotency key and a payload, and reads the persisted ``AgentRun`` back.
The runtime stays the only owner of run state, retries and duplicate
suppression, so an API call behaves exactly like a worker driving the
same queue.

Transaction discipline follows the runtime's own contract. The caller's
session commits at three points — after the run is created (releasing the
idempotency advisory lock), after the claim (releasing the row lock before
any external call) and after execution (making the outcome durable).
Nothing but the run row itself is written between those points, and the
handlers keep their own database work out of every external call.

The claim is by run id, never ``claim_next``: a request executes only the
run it just created or resumed, so it can never claim, execute or return a
different run — in particular another candidate's private run. A pending
run left behind by a concurrent duplicate is not drained as a side effect;
isolation takes priority over opportunistically emptying the queue.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime, HandlerRegistry
from app.api.errors import recorded_failure
from app.api.schemas import AgentRunOut
from app.models.agent_run import AgentRun
from app.models.enums import RunStatus


def run_agent_now(
    session: Session,
    *,
    registry: HandlerRegistry,
    agent_name: str,
    idempotency_key: str,
    payload: Mapping[str, object] | None = None,
    job_id: UUID | None = None,
    candidate_id: UUID | None = None,
) -> AgentRun:
    """Create, claim and execute one run, then return it settled.

    ``create_run`` may hand back a run that already owns the key — a
    succeeded one returns immediately with its stored output, an in-flight
    one is reused. The run is then claimed by its own id: a run already
    settled or held elsewhere yields no claim and is returned as-is, and no
    other queued run is ever touched. A retryable failure leaves the run
    ``pending`` for a later attempt rather than spinning here.
    """
    runtime = AgentRuntime(session, registry=registry)
    run = runtime.create_run(
        agent_name=agent_name,
        idempotency_key=idempotency_key,
        input=payload,
        job_id=job_id,
        candidate_id=candidate_id,
    )
    run_id = run.id
    session.commit()

    claimed = runtime.claim_run(run_id)
    if claimed is None:
        return run
    session.commit()
    runtime.execute(claimed)
    session.commit()
    return claimed


def agent_run_out(run: AgentRun) -> AgentRunOut:
    """Render a run as the public response schema."""
    return AgentRunOut.model_validate(run, from_attributes=True)


def agent_run_response(run: AgentRun) -> AgentRunOut:
    """Return the run, or raise the HTTP error its recorded failure maps to.

    A succeeded run is a 200 with the structured output. Any run carrying
    a recorded failure — still ``pending`` while retries remain, or
    terminal ``failed`` — becomes a mapped 4xx/503 with the run embedded
    in the detail, so the caller can tell a validation failure from an
    agent failure and can still read the run id and status. A run with no
    failure but no success either (claimed elsewhere, cancelled) is
    returned as it stands.
    """
    if run.status == RunStatus.succeeded:
        return agent_run_out(run)
    if run.error:
        failure = recorded_failure(run.error)
        raise HTTPException(
            status_code=failure.status_code,
            detail={
                "message": failure.message,
                "run": agent_run_out(run).model_dump(mode="json"),
            },
        )
    return agent_run_out(run)


__all__ = ["agent_run_out", "agent_run_response", "run_agent_now"]
