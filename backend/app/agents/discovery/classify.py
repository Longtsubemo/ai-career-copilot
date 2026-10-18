"""Deterministic URL and page quality classification for discovery.

Classification is evidence-based and LLM-free. A search hit is placed in
exactly one of three buckets:

* ``individual`` — evidence that the URL or page names one specific vacancy;
* ``listing`` — a general search, category, aggregator or listing page with
  no clearly identified individual vacancy;
* ``unknown`` — not enough evidence either way.

A title alone is never treated as individual-vacancy evidence. URL patterns
come first, then page metadata/content refines an ``unknown`` URL. Listing
pages are what the discovery agent skips; uncertain results are retained as
``unknown`` and are never labelled available until an individual vacancy is
actually verified.
"""

from __future__ import annotations

import re
from enum import StrEnum
from urllib.parse import SplitResult, parse_qsl, unquote, urlsplit, urlunsplit

from app.clients.firecrawl import CrawlResult

INDIVIDUAL_PARAM_KEYS = frozenset(
    {
        "currentjobid",
        "gh_jid",
        "job_id",
        "jobid",
        "job_posting_id",
        "jk",
        "posting_id",
        "postingid",
        "vjk",
    }
)

_SEARCH_QUERY_KEYS = frozenset(
    {"keywords", "q", "query", "search", "term"}
)

_STRONG_INDIVIDUAL_SEGMENTS = frozenset(
    {
        "job",
        "job-details",
        "job-posting",
        "job_posting",
        "jobdetail",
        "jobs",
        "opening",
        "openings",
        "opportunity",
        "opportunities",
        "position",
        "positions",
        "posting",
        "postings",
        "role",
        "roles",
        "vacancies",
        "vacancy",
        "view",
    }
)

_WEAK_INDIVIDUAL_SEGMENTS = frozenset({"career", "careers", "join", "work"})

_LISTING_ROOTS = frozenset(
    {
        "all-jobs",
        "browse",
        "career",
        "careers",
        "categories",
        "category",
        "collections",
        "companies",
        "company",
        "index",
        "job",
        "job-search",
        "jobs",
        "jobs-search",
        "new-jobs",
        "openings",
        "positions",
        "remote-jobs",
        "results",
        "roles",
        "search",
        "tags",
        "vacancies",
    }
)

_LISTING_PATH_MARKERS = (
    "/search",
    "/results",
    "/browse",
    "/category/",
    "/categories/",
    "/collections/",
    "/tags/",
    "/companies/",
    "/company/",
)

_GENERIC_TOKENS = frozenset(
    {
        "all",
        "browse",
        "categories",
        "category",
        "index",
        "job",
        "jobs",
        "list",
        "new",
        "page",
        "results",
        "search",
    }
)

_LISTING_TEXT_MARKERS = (
    "browse all jobs",
    "find your next job",
    "job search results",
    "jobs in ",
    "search results",
    "see all jobs",
    "view all jobs",
)

_JOB_POSTING_METADATA_KEYS = (
    "hiringorganization",
    "joblocation",
    "basesalary",
    "dateposted",
)

_DIGITS = re.compile(r"^\d+$")
_HEX_OR_UUID = re.compile(r"^[0-9a-f]{8,}$", re.IGNORECASE)
UUID_LIKE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_CLOSURE_MARKERS = (
    "applications are closed",
    "job has been filled",
    "job posting has expired",
    "no longer accepting applications",
    "position has been filled",
    "this job has closed",
    "this job posting is closed",
    "this position is closed",
    "this role is no longer available",
    "vacancy has been filled",
)

_URL_MAX_LENGTH = 300
_TOKEN_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_JWT_PATH_SEGMENT = re.compile(
    r"^[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}$"
)

_STATUS_METADATA_KEYS = ("status", "status_code", "statuscode")


class UrlQuality(StrEnum):
    """How confidently a URL or page names one individual vacancy."""

    individual = "individual"
    listing = "listing"
    unknown = "unknown"


def classify_url(url: str) -> UrlQuality:
    """Classify a URL by its own shape, without consulting page content."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return UrlQuality.unknown

    segments = [segment for segment in parts.path.split("/") if segment]
    for index, segment in enumerate(segments[:-1]):
        following = segments[index + 1]
        if segment.casefold() in _STRONG_INDIVIDUAL_SEGMENTS and _is_specific(
            following
        ):
            return UrlQuality.individual
        if segment.casefold() in _WEAK_INDIVIDUAL_SEGMENTS and _looks_like_id(
            following
        ):
            return UrlQuality.individual

    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.casefold() in INDIVIDUAL_PARAM_KEYS and value.strip():
            return UrlQuality.individual

    if _is_listing_path(parts.path, segments):
        return UrlQuality.listing

    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.casefold() in _SEARCH_QUERY_KEYS and value.strip():
            return UrlQuality.listing

    return UrlQuality.unknown


def classify_page(crawl: CrawlResult | None) -> UrlQuality:
    """Refine a URL classification using page metadata and content."""
    if crawl is None:
        return UrlQuality.unknown

    metadata = {str(key).casefold(): value for key, value in crawl.metadata.items()}
    document_type = metadata.get("@type")
    if isinstance(document_type, str) and document_type.casefold() == "jobposting":
        return UrlQuality.individual
    if any(
        isinstance(metadata.get(key), str) and metadata[key].strip()
        for key in _JOB_POSTING_METADATA_KEYS
    ):
        return UrlQuality.individual

    if has_listing_text(crawl):
        return UrlQuality.listing

    return UrlQuality.unknown


def has_listing_text(crawl: CrawlResult | None) -> bool:
    """Return whether page text contains a known search/listing marker."""
    if crawl is None or not isinstance(crawl.markdown, str):
        return False
    haystack = crawl.markdown.casefold()
    return any(marker in haystack for marker in _LISTING_TEXT_MARKERS)


def resolve_quality(url_quality: UrlQuality, page_quality: UrlQuality) -> UrlQuality:
    """Combine URL and page evidence; positive individual evidence wins."""
    if UrlQuality.individual in (url_quality, page_quality):
        return UrlQuality.individual
    if UrlQuality.listing in (url_quality, page_quality):
        return UrlQuality.listing
    return UrlQuality.unknown


def has_closure_evidence(crawl: CrawlResult | None) -> bool:
    """Return True when the page affirmatively states the vacancy is closed."""
    if crawl is None:
        return False
    if crawl_http_status(crawl) == 410:
        return True
    candidates = list(crawl.metadata.values())
    if crawl.markdown:
        candidates.append(crawl.markdown)
    for value in candidates:
        if not isinstance(value, str):
            continue
        lowered = value.casefold()
        if any(marker in lowered for marker in _CLOSURE_MARKERS):
            return True
    return False


def has_usable_content(crawl: CrawlResult | None) -> bool:
    """Return True when the crawl returned anything the classifiers can use."""
    if crawl is None:
        return False
    if isinstance(crawl.markdown, str) and crawl.markdown.strip():
        return True
    return any(
        isinstance(value, str) and value.strip() for value in crawl.metadata.values()
    )


def crawl_http_status(crawl: CrawlResult | None) -> int | None:
    """Return the HTTP status the crawler reported for the page, if any."""
    if crawl is None:
        return None
    metadata = {str(key).casefold(): value for key, value in crawl.metadata.items()}
    for key in _STATUS_METADATA_KEYS:
        value = metadata.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _validated_url_origin(url: str) -> tuple[SplitResult, str] | None:
    """Parse an HTTP(S) URL and return its safe origin components."""
    if not isinstance(url, str) or any(
        char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F or char == "\\"
        for char in url
    ):
        return None
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.netloc or not host:
        return None
    if any(char.isspace() or char in "/\\?#@[]" for char in host):
        return None
    authority = parts.netloc.rsplit("@", 1)[-1]
    if authority.endswith(":"):
        return None
    host = host.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    origin = urlunsplit((scheme, netloc, "", "", ""))
    if len(origin) > _URL_MAX_LENGTH:
        return None
    return parts, origin


def sanitize_public_url(url: str) -> str:
    """Return only a validated HTTP(S) origin for public discovery output."""
    parsed = _validated_url_origin(url)
    if parsed is None:
        return "<invalid-url>"
    return parsed[1]


def sanitize_url(url: str) -> str:
    """Return a bounded diagnostic URL with sensitive components removed."""
    parsed = _validated_url_origin(url)
    if parsed is None:
        return "<invalid-url>"
    parts, origin = parsed
    safe_segments = []
    for segment in parts.path.split("/"):
        decoded = unquote(segment)
        if _TOKEN_PATH_SEGMENT.fullmatch(decoded) or _JWT_PATH_SEGMENT.fullmatch(
            decoded
        ):
            safe_segments.append("[redacted]")
        else:
            safe_segments.append(segment)
    path = "/".join(safe_segments)
    return f"{origin}{path}"[:_URL_MAX_LENGTH]


def url_host(url: str) -> str:
    """Return the lowercased host of ``url`` without its port."""
    return (urlsplit(url.strip()).hostname or "").lower()


def _is_specific(token: str) -> bool:
    return bool(token) and token.casefold() not in _GENERIC_TOKENS


def _looks_like_id(token: str) -> bool:
    lowered = token.casefold()
    if _DIGITS.match(lowered) or _HEX_OR_UUID.match(lowered) or UUID_LIKE.match(
        lowered
    ):
        return True
    return len(lowered) >= 6 and any(character.isdigit() for character in lowered)


def _is_listing_path(path: str, segments: list[str]) -> bool:
    lowered_path = path.casefold()
    if any(marker in lowered_path for marker in _LISTING_PATH_MARKERS):
        return True
    if segments and segments[-1].casefold() in _LISTING_ROOTS:
        return True
    return False


__all__ = [
    "INDIVIDUAL_PARAM_KEYS",
    "UrlQuality",
    "classify_page",
    "classify_url",
    "crawl_http_status",
    "has_closure_evidence",
    "has_listing_text",
    "has_usable_content",
    "resolve_quality",
    "sanitize_public_url",
    "sanitize_url",
    "url_host",
]
