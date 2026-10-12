"""Deterministic relevance rules for discovery: location and role matching.

Everything here is pure and LLM-free. Two jobs are done, and nothing else:

* location normalization, so spelling, case and a small set of well-established
  aliases compare equal (``Bengaluru`` == ``Bangalore``) without ever rewriting
  the stored display value;
* a small, conservative role taxonomy, so equivalent title variants match
  exactly (``AI Engineer`` == ``Artificial Intelligence Engineer``) and
  genuinely related families (Generative AI, LLM, Machine Learning) are
  recognised as related without being collapsed into one another.

The taxonomy is intentionally narrow. A role outside it falls back to the
original token-overlap behaviour, and a title that carries no family signal is
never matched to a family request — ``Engineer`` must not match every
engineering role.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

_TOKENS = re.compile(r"[a-z0-9]+")

LOCATION_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("bangalore", "bengaluru"),
    ("mumbai", "bombay"),
    ("chennai", "madras"),
    ("kolkata", "calcutta"),
)

_LOCATION_CANONICAL: dict[str, str] = {
    member: group[0] for group in LOCATION_ALIAS_GROUPS for member in group
}

ROLE_FAMILIES: dict[str, tuple[tuple[str, ...], ...]] = {
    "generative_ai": (
        ("generative", "ai", "engineer"),
        ("gen", "ai", "engineer"),
    ),
    "llm": (
        ("llm", "engineer"),
        ("large", "language", "model", "engineer"),
        ("large", "language", "models", "engineer"),
    ),
    "ml": (
        ("ml", "engineer"),
        ("machine", "learning", "engineer"),
    ),
    "ai": (
        ("ai", "engineer"),
        ("artificial", "intelligence", "engineer"),
    ),
}

RELATED_ROLE_FAMILIES: dict[str, frozenset[str]] = {
    "ai": frozenset({"ml", "llm", "generative_ai"}),
    "ml": frozenset({"ai", "llm", "generative_ai"}),
    "llm": frozenset({"ai", "ml", "generative_ai"}),
    "generative_ai": frozenset({"ai", "ml", "llm"}),
}

ROLE_ALIASES: dict[str, str] = {
    "ai engineer": "Artificial Intelligence Engineer",
    "artificial intelligence engineer": "AI Engineer",
    "ml engineer": "Machine Learning Engineer",
    "machine learning engineer": "ML Engineer",
    "llm engineer": "Large Language Model Engineer",
    "large language model engineer": "LLM Engineer",
    "gen ai engineer": "Generative AI Engineer",
    "generative ai engineer": "Gen AI Engineer",
}


class RoleMatch(StrEnum):
    """How a job title relates to the requested role."""

    exact = "exact"
    related = "related"


def _fold(value: str) -> str:
    """Strip accents and casefold a value deterministically."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return without_accents.casefold()


def _tokens(value: str) -> list[str]:
    return _TOKENS.findall(_fold(value))


def normalize_location(value: str) -> str:
    """Return the canonical comparison form of a location string.

    Case, accents and punctuation are folded and known alias tokens are
    replaced by one canonical spelling (for example ``bengaluru`` ->
    ``bangalore``). This value is for comparison only; the caller keeps the
    original text for storage and display.
    """
    tokens = [_LOCATION_CANONICAL.get(token, token) for token in _tokens(value)]
    return " ".join(tokens)


def normalize_role(value: str) -> str:
    """Return the canonical comparison form of a role or title string."""
    return " ".join(_tokens(value))


def location_matches(requested: str, job_location: str | None) -> bool:
    """Return True when the requested and job locations share a place token.

    A missing or blank job location never conflicts, matching discovery's rule
    that absent evidence is never a rejection.
    """
    if not job_location:
        return True
    requested_tokens = set(normalize_location(requested).split())
    if not requested_tokens:
        return True
    return bool(requested_tokens & set(normalize_location(job_location).split()))


def role_family(text: str) -> str | None:
    """Classify a role or title into the smallest matching role family.

    A family matches only when every token of one of its phrases is present in
    the text, so ``AI Engineer`` and ``AI Platform Engineer`` match while a
    bare ``Engineer`` does not. Returns ``None`` when no family matches.
    """
    tokens = set(normalize_role(text).split())
    if not tokens:
        return None
    for family, phrases in ROLE_FAMILIES.items():
        for phrase in phrases:
            if set(phrase) <= tokens:
                return family
    return None


def related_families(family: str) -> frozenset[str]:
    """Return the families related to (but not identical to) ``family``."""
    return RELATED_ROLE_FAMILIES.get(family, frozenset())


__all__ = [
    "LOCATION_ALIAS_GROUPS",
    "RELATED_ROLE_FAMILIES",
    "ROLE_ALIASES",
    "ROLE_FAMILIES",
    "RoleMatch",
    "location_matches",
    "normalize_location",
    "normalize_role",
    "related_families",
    "role_family",
]
