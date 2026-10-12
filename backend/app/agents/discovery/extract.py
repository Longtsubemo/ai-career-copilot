"""Deterministic extraction and filtering for discovered job candidates.

Every function here is pure: fields are taken only from the search result
and the crawled page, never invented. Missing values stay ``None`` so the
database keeps its nullable semantics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.agents.discovery.contract import DiscoveryRequest
from app.agents.discovery.relevance import (
    RoleMatch,
    location_matches,
    related_families,
    role_family,
)
from app.clients.firecrawl import CrawlResult
from app.clients.searxng import SearchResult

TITLE_MAX = 255
LOCATION_MAX = 255
EMPLOYMENT_TYPE_MAX = 100
EXPERIENCE_LEVEL_MAX = 100
COMPANY_NAME_MAX = 255
DESCRIPTION_MAX = 20000

ROLE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)

_TOKENS = re.compile(r"[a-z0-9]+")

_LINE_FIELDS: dict[str, re.Pattern[str]] = {
    "location": re.compile(
        r"^\s*(?:location|job location|based in)\s*[:\-–]\s*(.+)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "employment_type": re.compile(
        r"^\s*(?:employment type|job type|type)\s*[:\-–]\s*(.+)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "experience_level": re.compile(
        r"^\s*(?:experience level|years of experience|experience)\s*[:\-–]\s*(.+)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "company_name": re.compile(
        r"^\s*(?:company name|company|employer)\s*[:\-–]\s*(.+)$",
        re.IGNORECASE | re.MULTILINE,
    ),
}

_YEAR_RANGE = re.compile(r"(\d+)\s*[-–—]\s*(\d+)\s*(?:years|year|yrs|yr)?")
_YEAR_PLUS = re.compile(r"(\d+)\s*(?:\+|plus)\s*(?:years|year|yrs|yr)?")
_YEARS_EXACT = re.compile(r"(\d+)\s*(?:years|year|yrs|yr)")
_OPEN_ENDED_CAP = 99

_METADATA_KEYS: dict[str, tuple[str, ...]] = {
    "title": ("title", "ogTitle", "twitterTitle"),
    "description": ("description", "ogDescription", "twitterDescription"),
    "location": ("location", "jobLocation", "addressLocality"),
    "employment_type": ("employmentType", "employment_type"),
    "experience_level": ("experienceLevel", "experience_level"),
    "company_name": ("company", "companyName", "company_name", "hiringOrganization"),
    "date_posted": ("datePosted", "date_posted", "dateposted", "datePublished"),
}

_DATE_LINE = re.compile(
    r"^\s*(?:date posted|posted|published|date published)\s*[:\-–]\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)

_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


@dataclass(frozen=True)
class ExtractedJob:
    """Job fields extracted from a search hit and its crawled page."""

    title: str
    job_url: str
    description: str | None = None
    location: str | None = None
    employment_type: str | None = None
    experience_level: str | None = None
    company_name: str | None = None
    date_posted: date | None = None


@dataclass(frozen=True)
class FilterDecision:
    """Whether a candidate passes the deterministic request filters.

    ``role_match`` records why the title was accepted: ``exact`` for a title in
    the requested role's own family (including its alias spellings) and
    ``related`` when it belongs to a related but non-identical family. It is
    ``None`` for the plain token-overlap fallback used by roles outside the
    taxonomy. ``reason`` explains every rejection and the classification behind
    a related-family acceptance.
    """

    accepted: bool
    reason: str | None = None
    role_match: RoleMatch | None = None


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit]


def _meta(crawl: CrawlResult | None, field: str) -> str | None:
    if crawl is None:
        return None
    lowered = {
        str(key).casefold(): value for key, value in crawl.metadata.items()
    }
    for key in _METADATA_KEYS[field]:
        value = lowered.get(key.casefold())
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_heading(markdown: str | None) -> str | None:
    if not markdown:
        return None
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return None


def _line_field(markdown: str | None, field: str) -> str | None:
    if not markdown:
        return None
    match = _LINE_FIELDS[field].search(markdown)
    if not match:
        return None
    value = " ".join(match.group(1).split())
    return value or None


def parse_posted_date(value: str | None) -> date | None:
    """Return a date only when ``value`` carries a reliable YYYY-MM-DD date.

    Anything that is not an explicit ISO-8601 date (with a bare ``YYYY-MM-DD``
    at the front, as ``datePosted`` normally provides) is ignored, so a vague
    "posted recently" string never becomes a fabricated date.
    """
    if not value or not value.strip():
        return None
    match = _ISO_DATE.match(value.strip())
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _date_posted(crawl: CrawlResult | None, markdown: str | None) -> date | None:
    raw = _meta(crawl, "date_posted")
    if raw is None and markdown:
        match = _DATE_LINE.search(markdown)
        if match:
            raw = match.group(1).strip()
    return parse_posted_date(raw)


def extract_job(result: SearchResult, crawl: CrawlResult | None) -> ExtractedJob:
    """Extract job fields from a search hit and, when available, its page.

    Priority is crawled-page metadata first, then deterministic markdown
    line patterns, then the search snippet. Values the sources do not
    provide stay ``None``; nothing is fabricated. The search title always
    yields a usable ``title``.
    """
    markdown = crawl.markdown if crawl is not None else None

    title = _meta(crawl, "title") or _first_heading(markdown) or result.title
    description = _meta(crawl, "description") or result.content
    location = _meta(crawl, "location") or _line_field(markdown, "location")
    employment_type = _meta(crawl, "employment_type") or _line_field(
        markdown, "employment_type"
    )
    experience_level = _meta(crawl, "experience_level") or _line_field(
        markdown, "experience_level"
    )
    company_name = _meta(crawl, "company_name") or _line_field(
        markdown, "company_name"
    )
    date_posted = _date_posted(crawl, markdown)

    return ExtractedJob(
        title=_clip(title, TITLE_MAX) or result.title[:TITLE_MAX],
        job_url=result.url,
        description=_clip(description, DESCRIPTION_MAX),
        location=_clip(location, LOCATION_MAX),
        employment_type=_clip(employment_type, EMPLOYMENT_TYPE_MAX),
        experience_level=_clip(experience_level, EXPERIENCE_LEVEL_MAX),
        company_name=_clip(company_name, COMPANY_NAME_MAX),
        date_posted=date_posted,
    )


def parse_year_range(text: str | None) -> tuple[int, int] | None:
    """Parse a year range such as ``"1-3 years"``, ``"5+"`` or ``"2 years"``.

    Returns ``None`` when the text carries no recognizable year range.
    """
    if not text:
        return None
    match = _YEAR_RANGE.search(text)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        if low <= high:
            return low, high
        return high, low
    match = _YEAR_PLUS.search(text)
    if match:
        years = int(match.group(1))
        return years, _OPEN_ENDED_CAP
    match = _YEARS_EXACT.search(text)
    if match:
        years = int(match.group(1))
        return years, years
    return None


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKENS.findall(text.casefold()))


def filter_check(request: DiscoveryRequest, job: ExtractedJob) -> FilterDecision:
    """Apply the request's role, location and experience to one candidate.

    The role is resolved through the conservative taxonomy when the request
    names a known family: a title in the same family (including its alias
    spellings) is an exact match and a title in a related family is accepted
    as related, while a title carrying no family signal or an unrelated family
    is rejected. Roles outside the taxonomy fall back to the original token
    overlap. Location is compared on normalized tokens so aliases such as
    ``Bengaluru``/``Bangalore`` agree. Experience year ranges must overlap
    when both sides parse. Fields the source did not provide are never used to
    reject a candidate.
    """
    matched: RoleMatch | None = None
    requested_family = role_family(request.role)
    if requested_family is not None:
        title_family = role_family(job.title)
        if title_family is None:
            return FilterDecision(
                False,
                f"title {job.title!r} carries no {requested_family!r} role signal",
            )
        if (
            title_family != requested_family
            and title_family not in related_families(requested_family)
        ):
            return FilterDecision(
                False,
                f"title {job.title!r} is role family {title_family!r}, "
                f"unrelated to requested {requested_family!r}",
            )
        matched = (
            RoleMatch.exact
            if title_family == requested_family
            else RoleMatch.related
        )
    else:
        role_tokens = frozenset(
            token for token in _tokens(request.role) if token not in ROLE_STOPWORDS
        )
        if role_tokens and not (role_tokens & _tokens(job.title)):
            missing = ", ".join(sorted(role_tokens))
            return FilterDecision(
                False, f"title {job.title!r} matches none of: {missing}"
            )

    if not location_matches(request.location, job.location):
        return FilterDecision(
            False,
            f"location {job.location!r} does not overlap {request.location!r}",
        )

    requested_range = parse_year_range(request.experience)
    job_range = parse_year_range(job.experience_level)
    if requested_range is not None and job_range is not None:
        requested_low, requested_high = requested_range
        job_low, job_high = job_range
        if requested_low > job_high or job_low > requested_high:
            return FilterDecision(
                False,
                f"experience {job.experience_level!r} does not overlap "
                f"{request.experience!r}",
            )

    reason = (
        f"title {job.title!r} matches related role family "
        f"{role_family(job.title)!r}"
        if matched is RoleMatch.related
        else None
    )
    return FilterDecision(True, reason=reason, role_match=matched)
