"""Deterministic state machine for ``AgentRun.status``.

The machine is the single authority on which status changes are legal::

    pending  -> running | cancelled
    running  -> succeeded | failed | pending (retry) | cancelled
    succeeded, failed, cancelled -> (terminal, no transitions)

Transitions are decided here in plain Python; an LLM is never consulted
about whether a state change is valid.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.agents.runtime.exceptions import InvalidTransitionError
from app.models.enums import RunStatus

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun

ALLOWED_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = {
    RunStatus.pending: frozenset({RunStatus.running, RunStatus.cancelled}),
    RunStatus.running: frozenset(
        {
            RunStatus.succeeded,
            RunStatus.failed,
            RunStatus.pending,
            RunStatus.cancelled,
        }
    ),
    RunStatus.succeeded: frozenset(),
    RunStatus.failed: frozenset(),
    RunStatus.cancelled: frozenset(),
}

TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    status for status, targets in ALLOWED_TRANSITIONS.items() if not targets
)


def can_transition(current: RunStatus, target: RunStatus) -> bool:
    """Return True when the state machine permits ``current -> target``."""
    return target in ALLOWED_TRANSITIONS[current]


def is_terminal(status: RunStatus) -> bool:
    """Return True when no transition can ever leave ``status``."""
    return status in TERMINAL_STATUSES


def ensure_transition(current: RunStatus, target: RunStatus) -> None:
    """Raise InvalidTransitionError unless ``current -> target`` is permitted."""
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


def apply_transition(run: AgentRun, target: RunStatus) -> None:
    """Validate and write ``target`` as the run's new status."""
    ensure_transition(run.status, target)
    run.status = target
