"""Deterministic search-query construction for discovery."""

from __future__ import annotations

from app.agents.discovery.contract import DiscoveryRequest


def build_search_query(request: DiscoveryRequest) -> str:
    """Combine the request constraints into one query string.

    The template is ``"{role} {location} jobs {experience}"`` — no LLM, no
    query planning, no tuning.
    """
    return " ".join(
        (request.role, request.location, "jobs", request.experience)
    )
