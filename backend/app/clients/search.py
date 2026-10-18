"""Shared contract for deterministic discovery search providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SearchError(RuntimeError):
    """A provider-neutral search transport, API, or response failure."""


@dataclass(frozen=True)
class SearchResult:
    """One normalized search hit; content is metadata, never page evidence."""

    url: str
    title: str
    content: str | None = None
    engine: str | None = None


class SearchClient(Protocol):
    """Small interface consumed by the discovery agent."""

    def search(self, query: str) -> list[SearchResult]: ...
