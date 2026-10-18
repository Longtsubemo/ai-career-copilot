"""Reviewed source registry and URL eligibility rules for discovery.

Source ownership and posting shape are checked before the existing page
quality classifier. ATS hosts are exact-match only. Employer and Workday
hosts require an explicit reviewed registration with path matchers; database
company URLs and arbitrary search-result domains are never trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote, urlsplit


class SourceKind(StrEnum):
    """URL-level result of applying the reviewed-source policy."""

    DIRECT_JOB = "direct_job"
    CAREER_LANDING = "career_landing"
    LISTING = "listing"
    UNSUPPORTED_SOURCE = "unsupported_source"
    UNKNOWN_URL = "unknown_url"
    INVALID_URL = "invalid_url"


@dataclass(frozen=True)
class SourceAssessment:
    kind: SourceKind
    provider: str | None = None

    @property
    def eligible(self) -> bool:
        return self.kind is SourceKind.DIRECT_JOB


@dataclass(frozen=True)
class ReviewedCareerSource:
    """One reviewed employer or Workday tenant registration.

    Hosts are exact DNS names. Path regexes are full-match rules maintained
    alongside the reviewed record; a host registration alone never makes all
    of its URLs eligible.
    """

    name: str
    hosts: frozenset[str]
    direct_job_paths: tuple[str, ...]
    career_landing_paths: tuple[str, ...] = ()
    listing_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.hosts:
            raise ValueError("reviewed source needs a name and at least one host")
        for host in self.hosts:
            if _normalize_host(host) != host:
                raise ValueError("reviewed source hosts must be normalized exact hosts")
        for pattern in (
            *self.direct_job_paths,
            *self.career_landing_paths,
            *self.listing_paths,
        ):
            re.compile(pattern, re.IGNORECASE)


@dataclass(frozen=True)
class SourceRegistry:
    """Immutable source allowlist, easy to extend with reviewed records."""

    employer_sources: tuple[ReviewedCareerSource, ...] = ()
    workday_tenants: tuple[ReviewedCareerSource, ...] = ()

    def __post_init__(self) -> None:
        seen: set[str] = set(_ATS_RULES)
        for record in (*self.employer_sources, *self.workday_tenants):
            overlap = seen.intersection(record.hosts)
            if overlap:
                raise ValueError("a source host can only have one registry rule")
            seen.update(record.hosts)
        for record in self.workday_tenants:
            if any(
                host != "myworkdayjobs.com" and not host.endswith(_WORKDAY_SUFFIX)
                for host in record.hosts
            ):
                raise ValueError("Workday tenant hosts must be exact myworkdayjobs.com hosts")

    @property
    def search_hosts(self) -> tuple[str, ...]:
        """Hosts suitable for bounded SearXNG site restrictions."""
        hosts = set(_ATS_RULES)
        for record in (*self.employer_sources, *self.workday_tenants):
            hosts.update(record.hosts)
        return tuple(sorted(hosts))


@dataclass(frozen=True)
class _HostRule:
    provider: str
    direct: tuple[str, ...]
    landing: tuple[str, ...]
    listing: tuple[str, ...]


_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"

# These URL shapes are grounded in provider-hosted posting examples and IDs.
# A board/company root is a landing page, never an individual vacancy.
_ATS_RULES: dict[str, _HostRule] = {
    "boards.greenhouse.io": _HostRule(
        provider="greenhouse",
        direct=(r"/[^/]+/jobs/[0-9]+",),
        landing=(r"/[^/]+",),
        listing=(r"/[^/]+/jobs",),
    ),
    "jobs.lever.co": _HostRule(
        provider="lever",
        direct=(rf"/[^/]+/{_UUID}",),
        landing=(r"/[^/]+",),
        listing=(r"/[^/]+/(?:postings|search|jobs)",),
    ),
    "jobs.ashbyhq.com": _HostRule(
        provider="ashby",
        direct=(rf"/[^/]+/{_UUID}",),
        landing=(r"/[^/]+",),
        listing=(r"/[^/]+/(?:jobs|search)",),
    ),
    "jobs.smartrecruiters.com": _HostRule(
        provider="smartrecruiters",
        direct=(r"/[^/]+/[0-9]+-[^/]+",),
        landing=(r"/[^/]+",),
        listing=(r"/[^/]+/(?:jobs|search)",),
    ),
}

DEFAULT_SOURCE_REGISTRY = SourceRegistry()

_WORKDAY_SUFFIX = ".myworkdayjobs.com"
_ENCODED_PATH_SEPARATOR = re.compile(r"%(?:25)*(?:2f|5c)", re.IGNORECASE)
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


def assess_source_url(
    url: str, registry: SourceRegistry = DEFAULT_SOURCE_REGISTRY
) -> SourceAssessment:
    """Classify a URL by reviewed host and provider-specific path shape.

    Only ``DIRECT_JOB`` is eligible to consume a candidate slot or reach
    Firecrawl. Query parameters and fragments never establish a posting.
    """
    parsed = _parse_http_url(url)
    if parsed is None:
        return SourceAssessment(SourceKind.INVALID_URL)
    host, path = parsed

    rule = _ATS_RULES.get(host)
    if rule is not None:
        return _classify_path(rule, path)

    for record in registry.employer_sources:
        if host in record.hosts:
            return _classify_registered(record, path, "employer")
    for record in registry.workday_tenants:
        if host in record.hosts:
            return _classify_registered(record, path, "workday")

    if host == "myworkdayjobs.com" or host.endswith(_WORKDAY_SUFFIX):
        return SourceAssessment(SourceKind.UNSUPPORTED_SOURCE, "workday")
    return SourceAssessment(SourceKind.UNSUPPORTED_SOURCE)


def _classify_path(rule: _HostRule, path: str) -> SourceAssessment:
    kind = _match_path(path, rule.direct, rule.landing, rule.listing)
    return SourceAssessment(kind, rule.provider)


def _classify_registered(
    record: ReviewedCareerSource, path: str, provider: str
) -> SourceAssessment:
    return SourceAssessment(
        _match_path(
            path,
            record.direct_job_paths,
            record.career_landing_paths,
            record.listing_paths,
        ),
        provider,
    )


def _match_path(
    path: str,
    direct: tuple[str, ...],
    landing: tuple[str, ...],
    listing: tuple[str, ...],
) -> SourceKind:
    normalized = path.rstrip("/") or "/"
    if any(re.fullmatch(pattern, normalized, re.IGNORECASE) for pattern in direct):
        return SourceKind.DIRECT_JOB
    if any(re.fullmatch(pattern, normalized, re.IGNORECASE) for pattern in landing):
        return SourceKind.CAREER_LANDING
    if any(re.fullmatch(pattern, normalized, re.IGNORECASE) for pattern in listing):
        return SourceKind.LISTING
    return SourceKind.UNKNOWN_URL


def _parse_http_url(url: object) -> tuple[str, str] | None:
    if not isinstance(url, str) or not url or any(
        char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F or char == "\\"
        for char in url
    ):
        return None
    try:
        parts = urlsplit(url)
        host_value = parts.hostname
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.netloc or not host_value:
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None
    host = _normalize_host(host_value)
    if not host or host != host_value.lower():
        return None
    if port is not None and port != (443 if scheme == "https" else 80):
        return None

    path = parts.path or "/"
    if _ENCODED_PATH_SEPARATOR.search(path):
        return None
    decoded_path = path
    for _ in range(3):
        decoded_path = unquote(decoded_path)
    if "\\" in decoded_path or any(
        ord(char) < 0x20 or ord(char) == 0x7F for char in decoded_path
    ):
        return None
    return host, decoded_path


def _normalize_host(host: str) -> str:
    if not isinstance(host, str) or not host or host.endswith("."):
        return ""
    normalized = host.lower()
    labels = normalized.split(".")
    if len(labels) < 2 or any(not _DNS_LABEL.fullmatch(label) for label in labels):
        return ""
    return normalized


__all__ = [
    "DEFAULT_SOURCE_REGISTRY",
    "ReviewedCareerSource",
    "SourceAssessment",
    "SourceKind",
    "SourceRegistry",
    "assess_source_url",
]
