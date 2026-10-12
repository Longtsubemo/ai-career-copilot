"""Registry mapping agent names to deterministic Python callables.

The registry is deliberately a flat name -> function mapping, not a plugin or
dependency-injection framework. Handlers are replaceable: a test or a future
milestone can register a different function under the same name without the
runtime knowing anything about it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agents.runtime.context import RunContext

Handler = Callable[[dict | None, "RunContext"], dict]


class HandlerRegistry:
    """A tiny ``agent_name -> handler`` mapping."""

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(
        self, agent_name: str, handler: Handler | None = None
    ) -> Handler | Callable[[Handler], Handler]:
        """Register a handler for ``agent_name``.

        Usable directly (``registry.register("x", fn)``) or as a decorator
        (``@registry.register("x")``). Registering the same name again
        replaces the previous handler.
        """
        if not agent_name:
            raise ValueError("agent_name must not be empty")

        def decorator(func: Handler) -> Handler:
            self._handlers[agent_name] = func
            return func

        if handler is None:
            return decorator
        return decorator(handler)

    def get(self, agent_name: str) -> Handler | None:
        """Return the handler registered for ``agent_name``, or None."""
        return self._handlers.get(agent_name)

    def names(self) -> frozenset[str]:
        """Return every registered agent name."""
        return frozenset(self._handlers)


default_registry = HandlerRegistry()


def register_handler(
    agent_name: str, handler: Handler | None = None
) -> Handler | Callable[[Handler], Handler]:
    """Register a handler in the process-wide default registry."""
    return default_registry.register(agent_name, handler)
