"""Execution context handed to an agent handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.models.agent_run import AgentRun


@dataclass(frozen=True)
class RunContext:
    """Minimal runtime facts a handler may rely on.

    A handler gets exactly this: the database session, the run being
    executed, the agent name and the 1-based attempt number. No wider
    application infrastructure is exposed.
    """

    session: Session
    run: AgentRun
    agent_name: str
    attempt: int
