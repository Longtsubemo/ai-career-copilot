"""Deterministic execution lifecycle for ``AgentRun`` rows.

The runtime is infrastructure, not an agent. It owns the run state machine —
create, claim, execute, complete, retry, fail, cancel — and delegates every
agent-specific decision to a registered handler. Nothing here calls an LLM or
knows what a Career Copilot agent does.

Transaction discipline: the runtime flushes but never commits; the caller owns
the transaction. A worker may commit right after :meth:`claim_next` to release
the row lock, or keep the transaction open so the claim and the handler's own
database work commit atomically together. A handler that aborts the caller's
database transaction cannot have its failure persisted — the database error
propagates to the caller instead of being recorded.
"""

from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.agents.runtime.context import RunContext
from app.agents.runtime.exceptions import (
    DuplicateSuccessError,
    HandlerNotRegisteredError,
    InvalidHandlerOutputError,
    InvalidRunInputError,
    InvalidRunStateError,
)
from app.agents.runtime.registry import HandlerRegistry, default_registry
from app.agents.runtime.state import apply_transition, ensure_transition
from app.models.agent_run import AgentRun
from app.models.enums import RunStatus

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
MAX_AGENT_NAME_LENGTH = 100
MAX_IDEMPOTENCY_KEY_LENGTH = 255

_IDEMPOTENCY_LOCK_NAMESPACE = "agent_run:idempotency"


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def format_error(error: BaseException | str) -> str:
    """Render an error as text storable in the ``agent_runs.error`` column."""
    if isinstance(error, str):
        return error
    return "".join(traceback.format_exception(error)).strip()


def _json_object(payload: Mapping[str, object]) -> dict:
    """Round-trip a mapping through JSON so only compatible data is persisted."""
    try:
        return json.loads(json.dumps(dict(payload), allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not JSON-compatible: {exc}") from exc


def validate_handler_output(output: object) -> dict:
    """Normalize a handler result into a plain JSON-compatible dictionary.

    Raises InvalidHandlerOutputError when the result is not a mapping or not
    JSON-compatible, so a malformed result becomes a recorded run failure
    instead of being persisted.
    """
    if not isinstance(output, Mapping):
        raise InvalidHandlerOutputError(
            f"handler output must be a mapping, got {type(output).__name__}"
        )
    try:
        return _json_object(output)
    except ValueError as exc:
        raise InvalidHandlerOutputError(str(exc)) from exc


class AgentRuntime:
    """Session-bound service driving ``AgentRun`` rows through their lifecycle.

    Lifecycle: :meth:`create_run` -> :meth:`claim_next` (shared queue) or
    :meth:`claim_run` (one specific run) -> :meth:`execute` -> complete /
    retry / fail, plus :meth:`cancel`. Every status change is validated
    against :data:`app.agents.runtime.state.ALLOWED_TRANSITIONS` before it is
    written, so terminal runs can never be restarted.
    """

    def __init__(
        self,
        session: Session,
        *,
        registry: HandlerRegistry | None = None,
        default_max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if default_max_attempts < 1:
            raise ValueError("default_max_attempts must be >= 1")
        self._session = session
        self._registry = registry if registry is not None else default_registry
        self._default_max_attempts = default_max_attempts
        self._clock = clock

    @property
    def registry(self) -> HandlerRegistry:
        """The handler registry this runtime executes handlers from."""
        return self._registry

    @property
    def session(self) -> Session:
        """The database session this runtime reads and writes through."""
        return self._session

    def create_run(
        self,
        *,
        agent_name: str,
        idempotency_key: str,
        input: Mapping[str, object] | None = None,
        job_id: UUID | None = None,
        candidate_id: UUID | None = None,
        max_attempts: int | None = None,
    ) -> AgentRun:
        """Create a pending run, or return the run that already owns the key.

        Idempotency semantics, backed by the partial unique index
        ``uq_agent_runs_idempotency_succeeded`` plus a transaction-scoped
        PostgreSQL advisory lock on the key:

        * a ``succeeded`` run with the key always wins — the caller gets that
          run back and no execution happens again;
        * a ``pending`` or ``running`` run with the key is reused, so
          duplicate submissions, including concurrent ones, collapse onto one
          run and the first input is preserved;
        * after a ``failed`` or ``cancelled`` run a new run is created,
          because a terminal failure or cancellation is meant to be
          re-triggered by a fresh submission.

        Keys are unique among succeeded runs across all agents, so callers
        must namespace them (``agent:subject:version``). The advisory lock is
        released when the caller's transaction ends; a caller that never
        commits never releases it.
        """
        self._validate_agent_name(agent_name)
        self._validate_idempotency_key(idempotency_key)
        attempts = (
            self._default_max_attempts if max_attempts is None else max_attempts
        )
        if attempts < 1:
            raise InvalidRunInputError(
                f"max_attempts must be >= 1, got {attempts}"
            )
        payload = None
        if input is not None:
            if not isinstance(input, Mapping):
                raise InvalidRunInputError(
                    f"input must be a mapping or None, got {type(input).__name__}"
                )
            try:
                payload = _json_object(input)
            except ValueError as exc:
                raise InvalidRunInputError(str(exc)) from exc

        self._lock_idempotency_key(idempotency_key)
        existing = self._existing_run_for(idempotency_key)
        if existing is not None:
            return existing

        run = AgentRun(
            agent_name=agent_name,
            status=RunStatus.pending,
            idempotency_key=idempotency_key,
            input=payload,
            job_id=job_id,
            candidate_id=candidate_id,
            max_attempts=attempts,
        )
        self._session.add(run)
        self._session.flush()
        return run

    def claim_next(self, *, agent_name: str | None = None) -> AgentRun | None:
        """Atomically claim the oldest eligible pending run, or return None.

        The row is selected with ``SELECT ... FOR UPDATE SKIP LOCKED``, so a
        worker skips rows another transaction has locked and two workers can
        never claim the same run. Claiming sets the status to ``running``,
        records ``started_at`` on the first attempt and increments
        ``attempt_count`` when a previously retried run is re-claimed, making
        ``attempt_count`` the 1-based ordinal of the attempt in progress.
        """
        stmt = (
            select(AgentRun)
            .where(AgentRun.status == RunStatus.pending)
            .order_by(AgentRun.created_at, AgentRun.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if agent_name is not None:
            stmt = stmt.where(AgentRun.agent_name == agent_name)
        run = self._session.execute(stmt).scalar_one_or_none()
        if run is None:
            return None
        return self._apply_claim(run)

    def claim_run(self, run_id: UUID) -> AgentRun | None:
        """Atomically claim one specific pending run, or return None.

        Unlike :meth:`claim_next`, this claims exactly ``run_id`` and never
        touches any other queued run. The row is selected with a blocking
        ``SELECT ... FOR UPDATE`` filtered to ``status = pending``, so a run
        another transaction already holds or settled yields None rather than
        executing twice or out of order. This is the claim used by a
        request-scoped caller that owns the run it just created: it keeps the
        run isolated to its own id instead of draining the shared queue.
        """
        stmt = (
            select(AgentRun)
            .where(AgentRun.id == run_id, AgentRun.status == RunStatus.pending)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        run = self._session.execute(stmt).scalar_one_or_none()
        if run is None:
            return None
        return self._apply_claim(run)

    def _apply_claim(self, run: AgentRun) -> AgentRun:
        """Move a freshly locked pending run to ``running`` and flush it."""
        apply_transition(run, RunStatus.running)
        if run.started_at is None:
            run.started_at = self._clock()
        else:
            run.attempt_count += 1
        self._session.flush()
        return run

    def execute(self, run: AgentRun) -> AgentRun:
        """Run the registered handler for a claimed run and settle its state.

        The run must be ``running`` (claim it first), otherwise
        InvalidRunStateError is raised before any handler code runs. Handler
        exceptions are never swallowed silently: they are logged, persisted in
        ``run.error`` and turned into a deterministic retry or failure by
        :meth:`fail`. ``execute`` returns the run in its new state —
        ``succeeded``, ``pending`` (retry scheduled) or ``failed`` — instead
        of raising, so callers settle the transaction by inspecting
        ``run.status``.
        """
        if run.status is not RunStatus.running:
            raise InvalidRunStateError(run.id, run.status, RunStatus.running)
        handler = self._registry.get(run.agent_name)
        if handler is None:
            return self.fail(run, HandlerNotRegisteredError(run.agent_name))
        context = RunContext(
            session=self._session,
            run=run,
            agent_name=run.agent_name,
            attempt=run.attempt_count,
        )
        try:
            output = validate_handler_output(handler(run.input, context))
        except Exception as exc:
            logger.exception(
                "agent %s run %s failed on attempt %s/%s",
                run.agent_name,
                run.id,
                run.attempt_count,
                run.max_attempts,
            )
            return self.fail(run, exc)
        return self.complete(run, output)

    def complete(self, run: AgentRun, output: Mapping[str, object]) -> AgentRun:
        """Validate ``output`` and settle the run as ``succeeded``.

        The input is preserved, any recorded error is cleared and
        ``finished_at`` is written. The idempotency key's advisory lock is
        taken before the write, so a duplicate run that succeeded elsewhere is
        detected deterministically and settled as a non-retryable failure
        while the original successful execution stays untouched. The partial
        unique index remains the backstop for writers that bypass the runtime.
        """
        validated = validate_handler_output(output)
        self._refresh_for_update(run)
        self._lock_idempotency_key(run.idempotency_key)
        if self._succeeded_elsewhere(run):
            logger.warning(
                "run %s could not succeed: idempotency key %r already succeeded",
                run.id,
                run.idempotency_key,
            )
            return self.fail(
                run,
                DuplicateSuccessError(run.idempotency_key),
                retryable=False,
            )
        apply_transition(run, RunStatus.succeeded)
        run.output = validated
        run.error = None
        run.finished_at = self._clock()
        self._session.flush()
        return run

    def fail(
        self,
        run: AgentRun,
        error: BaseException | str,
        *,
        retryable: bool = True,
    ) -> AgentRun:
        """Record ``error`` on a running run and settle its state.

        Retry policy (deterministic, no scheduler, no backoff): while the
        failure is ``retryable`` and ``attempt_count < max_attempts`` the run
        returns to ``pending`` so the next :meth:`claim_next` executes it
        again; otherwise it becomes terminal ``failed``. ``attempt_count`` is
        incremented by :meth:`claim_next` when a retried run is re-claimed, so
        a run executes at most ``max_attempts`` times in total.
        """
        self._refresh_for_update(run)
        run.error = format_error(error)
        exhausted = not retryable or run.attempt_count >= run.max_attempts
        target = RunStatus.failed if exhausted else RunStatus.pending
        if target is RunStatus.failed:
            run.finished_at = self._clock()
        apply_transition(run, target)
        self._session.flush()
        return run

    def cancel(self, run: AgentRun) -> AgentRun:
        """Cancel a pending or running run and record ``finished_at``.

        Terminal runs (``succeeded``, ``failed``, ``cancelled``) cannot be
        cancelled: InvalidTransitionError is raised and nothing is written.
        """
        self._refresh_for_update(run)
        apply_transition(run, RunStatus.cancelled)
        run.finished_at = self._clock()
        self._session.flush()
        return run

    def run_next(self, *, agent_name: str | None = None) -> AgentRun | None:
        """Claim and execute the oldest eligible run; None when none exists."""
        run = self.claim_next(agent_name=agent_name)
        if run is None:
            return None
        return self.execute(run)

    def _refresh_for_update(self, run: AgentRun) -> AgentRun:
        """Reload ``run`` under a row lock so transitions cannot race.

        Autoflush is disabled: the reload must read committed column state,
        and flushing unrelated pending objects from the caller as a side
        effect of the runtime's bookkeeping would be surprising.
        """
        stmt = (
            select(AgentRun)
            .where(AgentRun.id == run.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        with self._session.no_autoflush:
            return self._session.execute(stmt).scalar_one()

    def _lock_idempotency_key(self, idempotency_key: str) -> None:
        """Serialize all idempotency-key decisions across sessions.

        A transaction-scoped PostgreSQL advisory lock queues concurrent
        creators of the same key behind the winner, and also queues a
        completion behind other completions of the same key so a duplicate
        is detected before its write instead of racing the partial unique
        index. The lock is released when the caller's transaction commits
        or rolls back.
        """
        self._session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext(:namespace), hashtext(:key))"
            ),
            {"namespace": _IDEMPOTENCY_LOCK_NAMESPACE, "key": idempotency_key},
        )

    def _existing_run_for(self, idempotency_key: str) -> AgentRun | None:
        """Return the run that owns ``idempotency_key``, if one is usable.

        Preference order is ``succeeded`` first, then the oldest in-flight
        run; failed and cancelled runs are ignored so a new attempt can be
        started for the key.
        """
        runs = (
            self._session.execute(
                select(AgentRun)
                .where(AgentRun.idempotency_key == idempotency_key)
                .order_by(AgentRun.created_at, AgentRun.id)
                .execution_options(populate_existing=True)
            )
            .scalars()
            .all()
        )
        for status in (RunStatus.succeeded, RunStatus.running, RunStatus.pending):
            for run in runs:
                if run.status == status:
                    return run
        return None

    def _succeeded_elsewhere(self, run: AgentRun) -> bool:
        """Return whether a different run already succeeded under the key.

        Called by :meth:`complete` while holding the key's advisory lock, so
        concurrent completions of the same key serialize and the loser sees
        the winner's row instead of racing the partial unique index.
        """
        stmt = (
            select(AgentRun.id)
            .where(
                AgentRun.idempotency_key == run.idempotency_key,
                AgentRun.status == RunStatus.succeeded,
                AgentRun.id != run.id,
            )
            .limit(1)
        )
        with self._session.no_autoflush:
            return self._session.execute(stmt).first() is not None

    @staticmethod
    def _validate_agent_name(agent_name: str) -> None:
        if (
            not isinstance(agent_name, str)
            or not agent_name
            or len(agent_name) > MAX_AGENT_NAME_LENGTH
        ):
            raise InvalidRunInputError(
                "agent_name must be a non-empty string of at most "
                f"{MAX_AGENT_NAME_LENGTH} characters"
            )

    @staticmethod
    def _validate_idempotency_key(idempotency_key: str) -> None:
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key
            or len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH
        ):
            raise InvalidRunInputError(
                "idempotency_key must be a non-empty string of at most "
                f"{MAX_IDEMPOTENCY_KEY_LENGTH} characters"
            )
