"""Job Discovery agent — the first product agent on the AgentRuntime."""

from app.agents.discovery.agent import DiscoveryAgent, register_discovery_handler
from app.agents.discovery.classify import (
    UrlQuality,
    classify_page,
    classify_url,
    has_closure_evidence,
    has_usable_content,
    resolve_quality,
)
from app.agents.discovery.contract import (
    DISCOVERY_AGENT_NAME,
    MAX_DISCOVERY_ISSUES,
    DiscoveryInputError,
    DiscoveryIssue,
    DiscoveryIssueCategory,
    DiscoveryRequest,
    DiscoveryResult,
    discovery_idempotency_key,
)
from app.agents.discovery.extract import (
    ExtractedJob,
    FilterDecision,
    extract_job,
    filter_check,
    parse_posted_date,
)
from app.agents.discovery.query import build_search_queries, build_search_query
from app.agents.discovery.relevance import (
    RELATED_ROLE_FAMILIES,
    ROLE_ALIASES,
    ROLE_FAMILIES,
    RoleMatch,
    location_matches,
    normalize_location,
    normalize_role,
    related_families,
    role_family,
)

__all__ = [
    "DISCOVERY_AGENT_NAME",
    "MAX_DISCOVERY_ISSUES",
    "RELATED_ROLE_FAMILIES",
    "ROLE_ALIASES",
    "ROLE_FAMILIES",
    "DiscoveryAgent",
    "DiscoveryInputError",
    "DiscoveryIssue",
    "DiscoveryIssueCategory",
    "DiscoveryRequest",
    "DiscoveryResult",
    "ExtractedJob",
    "FilterDecision",
    "RoleMatch",
    "UrlQuality",
    "build_search_queries",
    "build_search_query",
    "classify_page",
    "classify_url",
    "discovery_idempotency_key",
    "extract_job",
    "filter_check",
    "has_closure_evidence",
    "has_usable_content",
    "location_matches",
    "normalize_location",
    "normalize_role",
    "parse_posted_date",
    "register_discovery_handler",
    "related_families",
    "resolve_quality",
    "role_family",
]
