"""Public surface of the generic Agent Runtime."""

from app.agents.runtime.context import RunContext
from app.agents.runtime.exceptions import (
    AgentRuntimeError,
    DuplicateSuccessError,
    HandlerNotRegisteredError,
    InvalidHandlerOutputError,
    InvalidRunInputError,
    InvalidRunStateError,
    InvalidTransitionError,
)
from app.agents.runtime.registry import (
    Handler,
    HandlerRegistry,
    default_registry,
    register_handler,
)
from app.agents.runtime.runtime import (
    DEFAULT_MAX_ATTEMPTS,
    AgentRuntime,
    validate_handler_output,
)
from app.agents.runtime.state import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    apply_transition,
    can_transition,
    ensure_transition,
    is_terminal,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "DEFAULT_MAX_ATTEMPTS",
    "TERMINAL_STATUSES",
    "AgentRuntime",
    "AgentRuntimeError",
    "DuplicateSuccessError",
    "Handler",
    "HandlerNotRegisteredError",
    "HandlerRegistry",
    "InvalidHandlerOutputError",
    "InvalidRunInputError",
    "InvalidRunStateError",
    "InvalidTransitionError",
    "RunContext",
    "apply_transition",
    "can_transition",
    "default_registry",
    "ensure_transition",
    "is_terminal",
    "register_handler",
    "validate_handler_output",
]
