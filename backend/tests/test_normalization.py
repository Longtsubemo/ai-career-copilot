"""Deterministic normalization used to build dedupe keys."""

import pytest

from app.services.normalization import canonicalize_url, normalize_company_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Google Inc.", "google"),
        ("Google LLC", "google"),
        ("GOOGLE", "google"),
        ("Acme, Inc.", "acme"),
        ("Pfizer Co.", "pfizer"),
        ("Siemens Aktiengesellschaft", "siemens"),
        ("  spaced   out  ", "spaced out"),
        ("Café Corp", "cafe"),
        ("The Company Store", "the company store"),
        ("Inc", "inc"),
        ("123 456", "123 456"),
    ],
)
def test_normalize_company_name(raw: str, expected: str) -> None:
    assert normalize_company_name(raw) == expected


def test_normalize_company_name_is_stable_across_call_equivalents() -> None:
    variants = ["Acme Inc.", "ACME, INC.", "acme  inc", "Acme   Inc"]
    assert len({normalize_company_name(variant) for variant in variants}) == 1


def test_normalize_company_name_keeps_meaningful_middle_tokens() -> None:
    assert normalize_company_name("Company International Ltd") == "company international"


def test_normalize_company_name_never_returns_empty_for_unusual_scripts() -> None:
    result = normalize_company_name("株式会社テスト")
    assert result != ""


def test_normalize_company_name_distinguishes_different_companies() -> None:
    assert normalize_company_name("Alpha Corp") != normalize_company_name("Beta Corp")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.com/jobs/1", "https://example.com/jobs/1"),
        ("https://www.example.com/jobs/1/", "https://example.com/jobs/1"),
        ("https://example.com/jobs/1?utm_source=linkedin", "https://example.com/jobs/1"),
        ("https://example.com/jobs/1?utm_campaign=x&refid=y", "https://example.com/jobs/1"),
        ("https://example.com/jobs/1#apply", "https://example.com/jobs/1"),
        ("https://example.com/jobs/1?b=2&a=1", "https://example.com/jobs/1?a=1&b=2"),
        ("https://example.com/jobs/1?gh_jid=99", "https://example.com/jobs/1"),
    ],
)
def test_canonicalize_url(raw: str, expected: str) -> None:
    assert canonicalize_url(raw) == expected


def test_canonicalize_url_collapses_tracking_variants_to_one_key() -> None:
    plain = canonicalize_url("https://example.com/jobs/1")
    for variant in (
        "https://www.example.com/jobs/1?utm_source=x",
        "https://EXAMPLE.com/jobs/1/?fbclid=abc",
        "https://example.com/jobs/1?trk=public#top",
    ):
        assert canonicalize_url(variant) == plain


def test_canonicalize_url_preserves_meaningful_query_parameters() -> None:
    assert (
        canonicalize_url("https://example.com/search?query=engineer")
        == "https://example.com/search?query=engineer"
    )