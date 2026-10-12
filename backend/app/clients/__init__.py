"""External tool clients used by product agents."""

from app.clients.firecrawl import (
    CrawlResult,
    FirecrawlClient,
    FirecrawlError,
    parse_scrape_response,
)
from app.clients.searxng import (
    SearchResult,
    SearXNGClient,
    SearXNGError,
    build_search_url,
    parse_search_response,
)

__all__ = [
    "CrawlResult",
    "FirecrawlClient",
    "FirecrawlError",
    "SearchResult",
    "SearXNGClient",
    "SearXNGError",
    "build_search_url",
    "parse_scrape_response",
    "parse_search_response",
]
