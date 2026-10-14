"""Deterministic search-query construction for discovery.

The primary query is the requested constraints verbatim. A single, well-grounded
role alias may be appended to improve recall (``AI Engineer`` also searches
``Artificial Intelligence Engineer``), but expansion is always bounded to a
small fixed maximum — never dozens of searches, never a location or experience
rewrite.
"""

from __future__ import annotations

from app.agents.discovery.contract import DiscoveryRequest
from app.agents.discovery.relevance import ROLE_ALIASES, normalize_role
from app.agents.discovery.sources import DEFAULT_SOURCE_REGISTRY, SourceRegistry

MAX_SEARCH_QUERIES = 2


def build_search_query(request: DiscoveryRequest) -> str:
    """Combine the request constraints into one query string.

    The template is ``"{role} {location} jobs {experience}"`` — no LLM, no
    query planning, no tuning.
    """
    return " ".join(
        (request.role, request.location, "jobs", request.experience)
    )


def build_search_queries(
    request: DiscoveryRequest,
    *,
    max_queries: int = MAX_SEARCH_QUERIES,
    source_registry: SourceRegistry = DEFAULT_SOURCE_REGISTRY,
) -> list[str]:
    """Return the bounded, ordered list of queries to run for a request.

    The first entry is always :func:`build_search_query`. When the requested
    role is exactly a known taxonomy phrase with a well-established alias, the
    alias query is appended if the budget allows. The result never repeats a
    query and never exceeds ``max_queries``.
    """
    if max_queries < 1:
        raise ValueError("max_queries must be >= 1")
    queries = [
        _restrict_to_supported_hosts(build_search_query(request), source_registry)
    ]
    alias = ROLE_ALIASES.get(normalize_role(request.role))
    if alias is not None and len(queries) < max_queries:
        candidate = " ".join(
            (alias, request.location, "jobs", request.experience)
        )
        candidate = _restrict_to_supported_hosts(candidate, source_registry)
        if candidate not in queries:
            queries.append(candidate)
    return queries


def _restrict_to_supported_hosts(query: str, registry: SourceRegistry) -> str:
    """Add a bounded-queries site hint; source eligibility remains mandatory."""
    site_clause = " OR ".join(f"site:{host}" for host in registry.search_hosts)
    return f"{query} ({site_clause})" if site_clause else query
