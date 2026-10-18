"""Relevance rules for discovery: location normalization, the role taxonomy
and the bounded query expansion.

Everything under test is pure and deterministic, so no database or fake
client is needed.
"""

from __future__ import annotations

import pytest

from app.agents.discovery import (
    RELATED_ROLE_FAMILIES,
    ROLE_FAMILIES,
    DiscoveryRequest,
    RoleMatch,
    build_search_queries,
    build_search_query,
    filter_check,
    location_matches,
    normalize_location,
    normalize_role,
    related_families,
    role_family,
)
from app.agents.discovery.extract import ExtractedJob

REQUEST = DiscoveryRequest(
    role="AI Engineer", location="Bengaluru", experience="1-3 years"
)
SUPPORTED_SITE_HINT = (
    "(site:boards.greenhouse.io OR site:jobs.ashbyhq.com OR "
    "site:jobs.lever.co OR site:jobs.smartrecruiters.com)"
)


def make_job(**overrides: object) -> ExtractedJob:
    base: dict = {
        "title": "Senior AI Engineer",
        "job_url": "https://example.com/jobs/1",
        "location": "Bengaluru",
        "experience_level": "2 years",
    }
    base.update(overrides)
    return ExtractedJob(**base)


class TestLocationNormalization:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("Bangalore", "bangalore"),
            ("  BENGALURU  ", "bangalore"),
            ("Bengaluru Urban, Karnataka", "bangalore urban karnataka"),
            ("Mumbai", "mumbai"),
            ("Bombay", "mumbai"),
            ("München", "munchen"),
            ("Pune-411001", "pune 411001"),
            ("", ""),
        ],
    )
    def test_normalize_location_is_canonical(self, value: str, expected: str) -> None:
        assert normalize_location(value) == expected

    def test_aliases_compare_equal(self) -> None:
        assert location_matches("Bangalore", "Bengaluru") is True
        assert location_matches("Bengaluru", "Bangalore") is True
        assert location_matches("Bangalore", "Bengaluru Urban, Karnataka") is True

    def test_disjoint_places_do_not_match(self) -> None:
        assert location_matches("Bangalore", "Mumbai") is False
        assert location_matches("Bengaluru", "Pune, India") is False

    def test_missing_job_location_never_conflicts(self) -> None:
        assert location_matches("Bangalore", None) is True
        assert location_matches("Bangalore", "") is True


class TestRoleNormalization:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("AI Engineer", "ai engineer"),
            ("  Senior   AI   Engineer ", "senior ai engineer"),
            ("ML/GenAI Engineer", "ml genai engineer"),
            ("", ""),
        ],
    )
    def test_normalize_role_folds_case_and_punctuation(
        self, value: str, expected: str
    ) -> None:
        assert normalize_role(value) == expected


class TestRoleFamily:
    @pytest.mark.parametrize(
        ("title", "family"),
        [
            ("AI Engineer", "ai"),
            ("Senior AI Engineer", "ai"),
            ("Artificial Intelligence Engineer", "ai"),
            ("AI Platform Engineer", "ai"),
            ("ML Engineer", "ml"),
            ("Senior Machine Learning Engineer", "ml"),
            ("LLM Engineer", "llm"),
            ("Large Language Model Engineer", "llm"),
            ("Generative AI Engineer", "generative_ai"),
            ("Gen AI Engineer", "generative_ai"),
        ],
    )
    def test_known_titles_map_to_a_family(self, title: str, family: str) -> None:
        assert role_family(title) == family

    @pytest.mark.parametrize(
        "title", ["Engineer", "Software Engineer", "Data Scientist", "Product Manager"]
    )
    def test_generic_titles_have_no_family(self, title: str) -> None:
        assert role_family(title) is None

    def test_families_are_mutually_related_but_never_their_own_relation(self) -> None:
        families = set(ROLE_FAMILIES)
        for family in families:
            related = related_families(family)
            assert family not in related
            assert related == families - {family}
        assert set(RELATED_ROLE_FAMILIES) == families


class TestRoleFiltering:
    def test_alias_titles_are_exact_matches(self) -> None:
        decision = filter_check(
            DiscoveryRequest(role="AI Engineer", location="Bengaluru", experience="x"),
            make_job(title="Artificial Intelligence Engineer", location=None),
        )
        assert decision.accepted
        assert decision.role_match is RoleMatch.exact

    def test_related_families_are_accepted_but_marked_related(self) -> None:
        for request_role, title in [
            ("AI Engineer", "Machine Learning Engineer"),
            ("ML Engineer", "Large Language Model Engineer"),
            ("Generative AI Engineer", "Machine Learning Engineer"),
        ]:
            decision = filter_check(
                DiscoveryRequest(role=request_role, location="Bengaluru", experience="x"),
                make_job(title=title, location=None),
            )
            assert decision.accepted, (request_role, title)
            assert decision.role_match is RoleMatch.related, (request_role, title)

    def test_ml_and_machine_learning_are_equivalent(self) -> None:
        decision = filter_check(
            DiscoveryRequest(role="ML Engineer", location="Bengaluru", experience="x"),
            make_job(title="Machine Learning Engineer", location=None),
        )
        assert decision.accepted
        assert decision.role_match is RoleMatch.exact

    def test_generic_engineer_does_not_match_a_family_request(self) -> None:
        decision = filter_check(
            DiscoveryRequest(role="AI Engineer", location="Bengaluru", experience="x"),
            make_job(title="Engineer", location=None),
        )
        assert not decision.accepted
        assert "role signal" in (decision.reason or "")


class TestBoundedQueryExpansion:
    def test_primary_query_is_the_request_verbatim(self) -> None:
        assert build_search_query(REQUEST) == "AI Engineer Bengaluru jobs 1-3 years"

    def test_known_role_appends_one_alias_query(self) -> None:
        assert build_search_queries(REQUEST) == [
            f"AI Engineer Bengaluru jobs 1-3 years {SUPPORTED_SITE_HINT}",
            f"Artificial Intelligence Engineer Bengaluru jobs 1-3 years {SUPPORTED_SITE_HINT}",
        ]

    def test_ml_alias_expansion(self) -> None:
        request = DiscoveryRequest(
            role="ML Engineer", location="Bangalore", experience="2 years"
        )
        assert build_search_queries(request) == [
            f"ML Engineer Bangalore jobs 2 years {SUPPORTED_SITE_HINT}",
            f"Machine Learning Engineer Bangalore jobs 2 years {SUPPORTED_SITE_HINT}",
        ]

    def test_role_outside_the_taxonomy_does_not_expand(self) -> None:
        request = DiscoveryRequest(
            role="Data Scientist", location="Bangalore", experience="1-3 years"
        )
        assert build_search_queries(request) == [
            f"Data Scientist Bangalore jobs 1-3 years {SUPPORTED_SITE_HINT}"
        ]

    def test_role_with_extra_tokens_does_not_expand(self) -> None:
        request = DiscoveryRequest(
            role="Senior AI Engineer", location="Bangalore", experience="1-3 years"
        )
        assert build_search_queries(request) == [
            f"Senior AI Engineer Bangalore jobs 1-3 years {SUPPORTED_SITE_HINT}"
        ]

    def test_max_queries_bounds_the_expansion(self) -> None:
        assert build_search_queries(REQUEST, max_queries=1) == [
            f"AI Engineer Bengaluru jobs 1-3 years {SUPPORTED_SITE_HINT}"
        ]

    def test_max_queries_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_queries"):
            build_search_queries(REQUEST, max_queries=0)

    def test_queries_are_unique_and_bounded(self) -> None:
        queries = build_search_queries(REQUEST)
        assert len(queries) == len(set(queries))
        assert len(queries) <= 2
