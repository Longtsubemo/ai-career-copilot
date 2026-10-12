"""Errors raised by the deterministic agent runtime.

Every exception here is raised by plain Python rules that inspect database
state. None of them is ever produced or decided by an LLM.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.models.enums import RunStatus


class AgentRuntimeError(Exception):
    """Base class for every error the agent runtime raises."""


class InvalidTransitionError(AgentRuntimeError):
    """A status change that the run state machine does not permit."""

    def __init__(self, current: RunStatus, target: RunStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"invalid agent run transition: {current.value!r} -> {target.value!r}"
        )


class InvalidRunStateError(AgentRuntimeError):
    """An operation was attempted on a run whose status does not allow it."""

    def __init__(self, run_id: UUID, current: RunStatus, required: RunStatus) -> None:
        self.run_id = run_id
        self.current = current
        self.required = required
        super().__init__(
            f"run {run_id} must be {required.value!r} but is {current.value!r}"
        )


class InvalidRunInputError(AgentRuntimeError):
    """``create_run`` received envelope or payload data it cannot persist."""


class InvalidHandlerOutputError(AgentRuntimeError):
    """A handler returned something that is not a JSON-compatible object."""


class HandlerNotRegisteredError(AgentRuntimeError):
    """No handler is registered for the run's agent name."""

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        super().__init__(f"no handler registered for agent {agent_name!r}")


class DuplicateSuccessError(AgentRuntimeError):
    """Another run already succeeded under the same idempotency key."""

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            f"a succeeded run already exists for idempotency key {idempotency_key!r}"
        )
