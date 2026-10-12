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
from app.agents.discovery.query import build_search_query

__all__ = [
    "DISCOVERY_AGENT_NAME",
    "MAX_DISCOVERY_ISSUES",
    "DiscoveryAgent",
    "DiscoveryInputError",
    "DiscoveryIssue",
    "DiscoveryIssueCategory",
    "DiscoveryRequest",
    "DiscoveryResult",
    "ExtractedJob",
    "FilterDecision",
    "UrlQuality",
    "build_search_query",
    "classify_page",
    "classify_url",
    "discovery_idempotency_key",
    "extract_job",
    "filter_check",
    "has_closure_evidence",
    "has_usable_content",
    "parse_posted_date",
    "register_discovery_handler",
    "resolve_quality",
]
