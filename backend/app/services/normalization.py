"""Deterministic text normalization helpers.

Every function here is pure and LLM-free. These values are persisted as dedupe
keys, so the same input must always produce the same output across processes and
runs.
"""

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

LEGAL_SUFFIX_TOKENS = frozenset(
    {
        "ab",
        "ag",
        "aktiengesellschaft",
        "as",
        "bv",
        "co",
        "company",
        "corp",
        "corporation",
        "gmbh",
        "inc",
        "incorporated",
        "limited",
        "llc",
        "llp",
        "ltd",
        "nv",
        "oy",
        "plc",
        "pte",
        "pty",
        "pvt",
        "sa",
        "sarl",
        "sas",
    }
)

TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "gh_src",
        "gh_jid",
        "refid",
        "source",
        "trk",
        "trackingid",
    }
)

_TRACKING_PREFIXES = ("utm_", "pk_", "pi_")

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def normalize_company_name(name: str) -> str:
    """Return a deterministic dedupe key for a company name.

    Strips accents, lowercases, collapses punctuation to single spaces and
    removes trailing legal suffixes such as ``Inc`` or ``Ltd``. Only trailing
    suffixes are removed so that names such as ``The Company Store`` survive
    intact.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    tokens = _NON_ALPHANUMERIC.sub(" ", without_accents.casefold()).split()

    while len(tokens) > 1 and tokens[-1] in LEGAL_SUFFIX_TOKENS:
        tokens.pop()

    normalized = " ".join(tokens)
    if normalized:
        return normalized

    collapsed = " ".join(without_accents.casefold().split())
    return collapsed


def canonicalize_url(url: str) -> str:
    """Return a deterministic dedupe key for a job posting URL.

    Lowercases scheme and host, drops ``www.``, removes tracking query
    parameters, sorts the remaining parameters and discards the fragment and any
    trailing slash.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()

    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port is not None:
        host = f"{host}:{parts.port}"

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_key(key)
    ]
    query = urlencode(sorted(query_pairs))

    path = parts.path.rstrip("/")
    if path == "" and parts.path != "":
        path = "/"

    return urlunsplit((scheme, host, path, query, ""))


def _is_tracking_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in TRACKING_QUERY_KEYS:
        return True
    return any(lowered.startswith(prefix) for prefix in _TRACKING_PREFIXES)