"""Deterministic extraction and filtering for discovered job candidates.

Every function here is pure: fields are taken only from the search result
and the crawled page, never invented. Missing values stay ``None`` so the
database keeps its nullable semantics.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.agents.discovery.contract import DiscoveryRequest
from app.agents.discovery.classify import (
    UrlQuality,
    classify_page,
    crawl_http_status,
    has_closure_evidence,
    has_listing_text,
)
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
# Discovery previously treated any non-empty crawl text as usable. A vacancy
# description needs enough page-derived detail to evaluate the role; these
# conservative bounds reject titles, snippets, navigation and short boilerplate.
MIN_DESCRIPTION_CHARS = 100
MIN_DESCRIPTION_WORDS = 15

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
_HTML_TAG = re.compile(r"<[^>]*>")
_MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKDOWN_DECORATION = re.compile(r"[`*_~>#]+")
_SPACE = re.compile(r"\s+")
_GENERIC_PAGE_TITLES = frozenset(
    {
        "career",
        "careers",
        "career opportunities",
        "job opportunities",
        "job search",
        "jobs",
        "open positions",
        "search jobs",
        "search results",
    }
)
_GENERIC_TITLE_PREFIXES = (
    "careers ",
    "career opportunities ",
    "jobs ",
    "open positions ",
)
_BOILERPLATE_MARKERS = (
    "browse all jobs",
    "explore our career opportunities",
    "find your next job",
    "join our talent community",
    "search our open positions",
    "view all open positions",
)
_CHALLENGE_MARKERS = (
    "access to this page has been denied",
    "access denied",
    "checking your browser",
    "complete the security check",
    "enable javascript and cookies",
    "human verification",
    "prove you are not a robot",
    "request blocked",
    "robot check",
    "security verification",
    "temporarily blocked",
    "unusual traffic",
    "verify you are human",
    "verify that you are human",
    "you have been blocked",
    "403 forbidden",
)
_PAGE_FAILURE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:we(?:'re| are| have|['’]ve)?\s+)?(?:currently\s+)?"
        r"(?:experiencing|encountering|having)\s+(?:an?\s+)?"
        r"(?:internal|application|server)(?:\s+server)?\s+error\b",
        r"\ban?\s+(?:internal|application) error (?:occurred|has occurred)\b",
        r"\b(?:this\s+|the\s+)?page\s+(?:has\s+)?"
        r"(?:encountered|hit)\s+(?:an?\s+)?"
        r"(?:internal|application|server)(?:\s+server)?\s+error\b",
        r"\ban error occurred\b",
        r"\b(?:unable\s+to|failed\s+to|cannot|can't|could not|couldn't)\s+"
        r"(?:load|display|show)\s+(?:this\s+|the\s+)?"
        r"(?:page|job|position|opportunity|content)\b",
        r"\b(?:page|job posting|position|opportunity)\s+"
        r"(?:was\s+|is\s+|could not be\s+)?(?:not found|unavailable)\b",
        r"\b(?:this\s+)?page\s+(?:is|was)\s+(?:currently\s+)?unavailable\b",
        r"\b404\s+(?:not found|error)\b",
        r"\b(?:an?\s+)?error occurred\s+(?:while|when)\s+"
        r"(?:loading|processing|displaying)\b",
        r"\b(?:bad gateway|gateway timeout|service unavailable|"
        r"upstream (?:connect )?error|this site can't be reached)\b",
        r"\b(?:service|system|site)\s+(?:is\s+)?(?:currently\s+)?"
        r"unavailable\b",
        r"\bwe (?:cannot|can't|couldn't) (?:display|show|load)\s+"
        r"(?:this\s+|the\s+)?page\b",
    )
)
_JOB_SECTION_HEADING = re.compile(
    r"^\s{0,3}#{1,6}\s*(?:responsibilities|what you(?:'|’)ll do|"
    r"what you will do|your role|requirements|qualifications|"
    r"what we(?:'|’)re looking for|what you(?:'|’)ll bring|skills)\b",
    re.IGNORECASE | re.MULTILINE,
)
_JOB_ACTION = re.compile(
    r"\b(?:analy[sz]e|architect|build|collaborate|create|deliver|design|"
    r"develop|implement|integrate|lead|maintain|manage|monitor|optimi[sz]e|"
    r"own|research|resolve|support|test|troubleshoot)\b",
    re.IGNORECASE,
)
_STRUCTURED_DATA_KEYS = ("jobposting", "jsonld", "structureddata", "structured_data")
_POSTING_TITLE_KEYS = ("title", "name")
_POSTING_DESCRIPTION_KEYS = ("description",)


class PageEvidenceFailure(StrEnum):
    """Bounded internal reason that a successful crawl was not a vacancy."""

    empty = "empty"
    listing = "listing"
    insufficient = "insufficient"
    challenge = "challenge"
    error = "error"


@dataclass(frozen=True)
class PageJobEvidence:
    """Job fields sourced only from the crawled page, never search metadata."""

    title: str | None
    description: str | None
    location: str | None = None
    employment_type: str | None = None
    experience_level: str | None = None
    company_name: str | None = None
    date_posted: date | None = None
    structured_job_posting: bool = False


@dataclass(frozen=True)
class PageValidation:
    """The result of checking a crawl for sufficient vacancy evidence."""

    extracted: ExtractedJob | None
    failure: PageEvidenceFailure | None = None

    @property
    def accepted(self) -> bool:
        return self.extracted is not None


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

    This compatibility helper may use search metadata as a fallback. It is not
    a vacancy validator and must not be used as persistence input; the agent
    uses :func:`extract_page_evidence` and :func:`validate_page_evidence`.

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


def _clean_page_text(value: str) -> str:
    value = html.unescape(value)
    value = _MARKDOWN_LINK.sub(r"\1", value)
    value = _HTML_TAG.sub(" ", value)
    value = _MARKDOWN_DECORATION.sub(" ", value)
    return _SPACE.sub(" ", value).strip()


def _page_body(markdown: str | None) -> str | None:
    if not markdown:
        return None
    lines: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if any(pattern.match(line) for pattern in _LINE_FIELDS.values()):
            continue
        if _DATE_LINE.match(line):
            continue
        lines.append(stripped)
    cleaned = _clean_page_text(" ".join(lines))
    return cleaned or None


def _is_job_posting(value: Mapping[str, object]) -> bool:
    raw_type = value.get("@type")
    types = raw_type if isinstance(raw_type, Sequence) and not isinstance(
        raw_type, (str, bytes)
    ) else (raw_type,)
    return any(
        isinstance(item, str) and item.casefold() == "jobposting"
        for item in types
    )


def _posting_nodes(value: object, *, depth: int = 0) -> list[Mapping[str, object]]:
    """Find bounded JSON-LD JobPosting objects in Firecrawl page metadata."""
    if depth > 12:
        return []
    if isinstance(value, str) and len(value) <= 100_000:
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                value = json.loads(stripped)
            except (TypeError, ValueError):
                return []
    if isinstance(value, Mapping):
        found = [_posting_node(value)] if _is_job_posting(value) else []
        for key, child in value.items():
            if str(key).casefold() in {"@graph", "itemlistelement", *_STRUCTURED_DATA_KEYS}:
                found.extend(_posting_nodes(child, depth=depth + 1))
        return found
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        found: list[Mapping[str, object]] = []
        for child in value[:100]:
            found.extend(_posting_nodes(child, depth=depth + 1))
        return found
    return []


def _posting_node(value: Mapping[str, object]) -> Mapping[str, object]:
    # ``@type`` is checked by the caller; copy avoids retaining provider-owned
    # mapping implementations in the validated extraction result.
    return dict(value)


def _posting_value(
    posting: Mapping[str, object] | None, keys: Sequence[str]
) -> object | None:
    if posting is None:
        return None
    lowered = {str(key).casefold(): value for key, value in posting.items()}
    for key in keys:
        value = lowered.get(key.casefold())
        if value is not None:
            return value
    return None


def _string_value(value: object | None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _nested_name(value: object | None) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        name = value.get("name")
        return _string_value(name)
    return None


def _posting_location(value: object | None) -> str | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            location = _posting_location(item)
            if location:
                return location
        return None
    if not isinstance(value, Mapping):
        return _string_value(value)
    address = value.get("address")
    if isinstance(address, Mapping):
        pieces = [
            _string_value(address.get(key))
            for key in ("addressLocality", "addressRegion", "addressCountry")
        ]
        return ", ".join(piece for piece in pieces if piece) or None
    return _string_value(value.get("name"))


def extract_page_evidence(crawl: CrawlResult | None) -> PageJobEvidence:
    """Extract candidate fields from the crawled page only.

    Search-result title and snippet are deliberately not accepted by this
    function. Structured JobPosting fields take priority, followed by page
    metadata and page markdown.
    """
    if crawl is None:
        return PageJobEvidence(title=None, description=None)

    postings: list[Mapping[str, object]] = []
    if _is_job_posting(crawl.metadata):
        postings.append(dict(crawl.metadata))
    for key, value in crawl.metadata.items():
        if str(key).casefold() in _STRUCTURED_DATA_KEYS:
            postings.extend(_posting_nodes(value))
    posting = postings[0] if postings else None

    structured_title = _string_value(
        _posting_value(posting, _POSTING_TITLE_KEYS)
    )
    structured_description = _string_value(
        _posting_value(posting, _POSTING_DESCRIPTION_KEYS)
    )
    title = structured_title or _meta(crawl, "title") or _first_heading(crawl.markdown)
    description = (
        structured_description
        or _meta(crawl, "description")
        or _page_body(crawl.markdown)
    )

    location = _posting_location(_posting_value(posting, ("jobLocation",)))
    location = location or _meta(crawl, "location") or _line_field(
        crawl.markdown, "location"
    )
    employment_type = _string_value(
        _posting_value(posting, ("employmentType",))
    ) or _meta(crawl, "employment_type") or _line_field(
        crawl.markdown, "employment_type"
    )
    experience = _string_value(
        _posting_value(posting, ("experienceRequirements",))
    ) or _meta(crawl, "experience_level") or _line_field(
        crawl.markdown, "experience_level"
    )
    company = _nested_name(_posting_value(posting, ("hiringOrganization",)))
    company = company or _meta(crawl, "company_name") or _line_field(
        crawl.markdown, "company_name"
    )
    date_posted = parse_posted_date(
        _string_value(_posting_value(posting, ("datePosted",)))
    ) or _date_posted(crawl, crawl.markdown)

    return PageJobEvidence(
        title=_clip(title, TITLE_MAX),
        description=_clip(_clean_page_text(description), DESCRIPTION_MAX)
        if description
        else None,
        location=_clip(location, LOCATION_MAX),
        employment_type=_clip(employment_type, EMPLOYMENT_TYPE_MAX),
        experience_level=_clip(experience, EXPERIENCE_LEVEL_MAX),
        company_name=_clip(company, COMPANY_NAME_MAX),
        date_posted=date_posted,
        structured_job_posting=posting is not None,
    )


def _substantive_description(value: str | None) -> bool:
    if not value:
        return False
    cleaned = _clean_page_text(value)
    words = re.findall(r"[A-Za-z0-9]+", cleaned)
    return (
        len(cleaned) >= MIN_DESCRIPTION_CHARS
        and len(words) >= MIN_DESCRIPTION_WORDS
        and not any(marker in cleaned.casefold() for marker in _BOILERPLATE_MARKERS)
    )


def _is_generic_title(value: str) -> bool:
    normalized = " ".join(_TOKENS.findall(value.casefold()))
    return (
        not normalized
        or normalized in _GENERIC_PAGE_TITLES
        or any(normalized.startswith(prefix) for prefix in _GENERIC_TITLE_PREFIXES)
    )


def _is_challenge_page(crawl: CrawlResult, evidence: PageJobEvidence) -> bool:
    status = crawl_http_status(crawl)
    if status is not None and status >= 400 and status != 410:
        return True
    opening = " ".join(
        part
        for part in (
            evidence.title or "",
            (crawl.markdown or "")[:1200],
            (evidence.description or "")[:1200],
        )
        if part
    ).casefold()
    return any(marker in opening for marker in _CHALLENGE_MARKERS)


def _page_headings(markdown: str | None) -> list[str]:
    if not markdown:
        return []
    headings: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if match:
            headings.append(match.group(1).strip()[:240])
            if len(headings) == 10:
                break
    return headings


def _page_failure_surface(crawl: CrawlResult, evidence: PageJobEvidence) -> str:
    """Return only page-title, heading, and opening text for failure checks.

    Restricting phrase checks to the page's surface avoids rejecting normal
    vacancy content that discusses errors or unavailable systems later on.
    """
    markdown = crawl.markdown or ""
    headings = "\n".join(_page_headings(markdown))
    return "\n".join(
        value[:1200]
        for value in (
            _meta(crawl, "title") or "",
            evidence.title or "",
            headings,
            markdown[:1200],
            evidence.description or "",
        )
        if value
    ).casefold()


_SOMETHING_WENT_WRONG = re.compile(r"\bsomething went wrong\b", re.IGNORECASE)
_SOMETHING_WENT_WRONG_FAILURE_CONTEXT = re.compile(
    r"\bsomething went wrong\b.{0,160}\b(?:please\s+)?"
    r"(?:refresh|reload|try again|contact (?:our )?support|"
    r"unable to|could not|failed to)\b"
    r"|\b(?:please\s+)?(?:refresh|reload|try again|"
    r"contact (?:our )?support|unable to|could not|failed to)\b"
    r".{0,160}\bsomething went wrong\b",
    re.IGNORECASE | re.DOTALL,
)


def _is_page_error(crawl: CrawlResult, evidence: PageJobEvidence) -> bool:
    surface = _page_failure_surface(crawl, evidence)
    page_identity = "\n".join(
        value
        for value in (
            _meta(crawl, "title") or "",
            evidence.title or "",
            *_page_headings(crawl.markdown),
        )
        if value
    )
    # The phrase is common in real incident-response responsibilities. Treat
    # it alone as a failure only when it identifies the page title or a
    # heading; in body/description text, require nearby recovery/error-page
    # instructions as additional context.
    if _SOMETHING_WENT_WRONG.search(page_identity):
        return True
    if _SOMETHING_WENT_WRONG_FAILURE_CONTEXT.search(surface):
        return True
    if any(pattern.search(surface) for pattern in _PAGE_FAILURE_PATTERNS):
        return True

    title_values = [_meta(crawl, "title") or "", evidence.title or ""]
    title_values.extend(_page_headings(crawl.markdown))
    failure_titles = {
        "application error",
        "bad gateway",
        "internal error",
        "internal server error",
        "page not found",
        "server error",
        "service unavailable",
    }
    return any(
        " ".join(_TOKENS.findall(title.casefold())) in failure_titles
        for title in title_values
        if title
    )


def _has_positive_job_evidence(
    crawl: CrawlResult, evidence: PageJobEvidence
) -> bool:
    """Require structured vacancy data or job-specific page content signals."""
    if evidence.structured_job_posting:
        return True
    markdown = crawl.markdown or ""
    if _JOB_SECTION_HEADING.search(markdown):
        return True
    # A single generic verb is weak evidence. Multiple distinct action terms
    # make a short error/status message less likely to pass as a description.
    actions = {
        match.casefold()
        for match in _JOB_ACTION.findall(evidence.description or "")
    }
    return len(actions) >= 2


def validate_page_evidence(
    crawl: CrawlResult | None, evidence: PageJobEvidence
) -> PageValidation:
    """Require page-derived evidence before a crawl can represent a vacancy."""
    has_text = crawl is not None and any(
        isinstance(value, str) and value.strip()
        for value in (crawl.markdown, *crawl.metadata.values())
    )
    if crawl is None or (not has_text and not evidence.structured_job_posting):
        return PageValidation(None, PageEvidenceFailure.empty)
    if _is_challenge_page(crawl, evidence):
        return PageValidation(None, PageEvidenceFailure.challenge)
    if _is_page_error(crawl, evidence):
        return PageValidation(None, PageEvidenceFailure.error)
    if (
        has_listing_text(crawl)
        or classify_page(crawl) is UrlQuality.listing
    ):
        return PageValidation(None, PageEvidenceFailure.listing)
    if crawl_http_status(crawl) == 410 and not has_closure_evidence(crawl):
        return PageValidation(None, PageEvidenceFailure.challenge)
    if (
        evidence.title is None
        or _is_generic_title(evidence.title)
        or not _substantive_description(evidence.description)
        or not _has_positive_job_evidence(crawl, evidence)
    ):
        return PageValidation(None, PageEvidenceFailure.insufficient)

    return PageValidation(
        ExtractedJob(
            title=evidence.title,
            job_url=crawl.url,
            description=evidence.description,
            location=evidence.location,
            employment_type=evidence.employment_type,
            experience_level=evidence.experience_level,
            company_name=evidence.company_name,
            date_posted=evidence.date_posted,
        )
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
