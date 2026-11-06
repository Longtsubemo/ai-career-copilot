"""Synthetic job-matching benchmark fixtures and deterministic checks.

The evaluator checks explicit rubric constraints only. It cannot establish
that free-text model claims are semantically grounded; those require review.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.job_matching.contract import MatchResult, MatchVerdict

SignalField = Literal[
    "experience_match", "education_match", "location_match", "seniority_match"
]
EXPECTED_SCENARIOS = frozenset(
    {
        "strong_match",
        "partial_match",
        "missing_essential_skills",
        "unrelated_role",
        "certification_not_mentioned",
        "certification_confirmed_absent",
        "preferred_skill_not_evidenced",
        "relevant_experience_gap",
        "missing_evidence",
        "transferable_experience",
    }
)
_GAP_PHRASE_ALIASES = {
    "production gpu training experience": (
        "experience with production gpu training",
        "production gpu training",
    ),
}
_TRANSFERABILITY_CUES = frozenset({"relevant", "related", "transferable"})
_TRANSFERABILITY_NEGATIONS = frozenset({"not", "never", "no", "without", "cannot"})
_CLAUSE_BREAK_RE = re.compile(
    r"[,;:.!?]+|\b(?:and|but|however|whereas|although|while|yet)\b",
    re.IGNORECASE,
)
_GAP_ENTRY_SENTENCE_RE = re.compile(r"[.!?;]+")

NOT_EVIDENCED_CUES = (
    "not evidenced",
    "not listed",
    "not provided",
    "not documented",
    "not mentioned",
    "does not mention",
    "no evidence of",
    "cannot verify",
    "cannot be verified",
    "not established",
    "lacks evidence of",
    "no documented",
    "no relevant",
)
CONFIRMED_ABSENCE_CUES = (
    "explicitly states they do not hold",
    "explicitly states they do not have",
    "does not hold",
    "does not have",
    "does not possess",
    "do not hold",
    "do not have",
    "do not possess",
    "has no",
    "never held",
    "never obtained",
)
ABSENCE_ASSERTION_CUES = CONFIRMED_ABSENCE_CUES + ("lacks",)
_POSITIVE_CLAIM_VERBS = frozenset(
    {
        "has",
        "holds",
        "possesses",
        "earned",
        "obtained",
        "completed",
        "certified",
        "claims",
        "reports",
        "demonstrates",
        "proves",
    }
)
_NEGATION_CUES = frozenset({"not", "never", "no", "without", "lacks", "missing"})
_POSITIVE_BRIDGE_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "required",
        "named",
        "professional",
        "experience",
        "with",
        "in",
        "for",
        "as",
    }
)


class JobEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    company_name: str | None = None
    location: str | None = None
    employment_type: str | None = None
    experience_level: str | None = None
    analysis: dict[str, Any]


class CandidateEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    resume_kind: str
    resume_content: str
    headline: str | None = None
    location: str | None = None
    years_of_experience: float | None = None
    seniority: str | None = None
    skills: list[str] | None = None
    summary: str | None = None


class CaseExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_verdicts: list[MatchVerdict] = Field(default_factory=list)
    must_match_skills: list[str] = Field(default_factory=list)
    must_be_missing_required: list[str] = Field(default_factory=list)
    must_be_missing_preferred: list[str] = Field(default_factory=list)
    must_not_be_missing_required: list[str] = Field(default_factory=list)
    must_be_matched: list[SignalField] = Field(default_factory=list)
    must_be_mismatched: list[SignalField] = Field(default_factory=list)
    must_be_unclear: list[SignalField] = Field(default_factory=list)
    must_not_claim_positive: list[str] = Field(default_factory=list)
    must_identify_gaps: list[str] = Field(default_factory=list)
    must_acknowledge_transferable: list[str] = Field(default_factory=list)
    must_describe_as_not_evidenced: list[str] = Field(default_factory=list)
    must_describe_as_confirmed_absent: list[str] = Field(default_factory=list)
    must_not_describe_as_confirmed_absent: list[str] = Field(default_factory=list)
    score_above: list[str] = Field(default_factory=list)


class MatchBenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    scenario: str
    job: JobEvidence
    candidate: CandidateEvidence
    expectations: CaseExpectations


def _normalize_phrase(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _phrase_variants(phrase: str) -> tuple[list[str], ...]:
    normalized = _normalize_phrase(phrase)
    variants = (normalized, *_GAP_PHRASE_ALIASES.get(normalized, ()))
    return tuple(tokens for variant in variants if (tokens := variant.split()))


def _phrase_spans(text: str, phrase: str) -> list[tuple[int, int, list[str]]]:
    """Return exact token-sequence matches, with punctuation as a boundary."""
    tokens = _normalize_phrase(text).split()
    spans: list[tuple[int, int, list[str]]] = []
    for variant in _phrase_variants(phrase):
        for start in range(len(tokens) - len(variant) + 1):
            if tokens[start : start + len(variant)] != variant:
                continue
            # A hyphenated "pre-production" is tokenized as "pre production";
            # do not mistake it for the production experience being evaluated.
            if variant[0] == "production" and start and tokens[start - 1] in {
                "pre",
                "non",
            }:
                continue
            spans.append((start, start + len(variant), variant))
    return spans


def _contains_token_phrase(text: str, phrase: str) -> bool:
    """Match a phrase on token boundaries rather than as an arbitrary substring."""
    return bool(_phrase_spans(text, phrase))


def _clauses(text: str) -> list[list[str]]:
    return [
        _normalize_phrase(part).split()
        for part in _CLAUSE_BREAK_RE.split(text)
        if _normalize_phrase(part)
    ]


def _gap_sentences(text: str) -> list[list[str]]:
    return [
        _normalize_phrase(part).split()
        for part in _GAP_ENTRY_SENTENCE_RE.split(text)
        if _normalize_phrase(part)
    ]


def _gap_phrase_is_identified(gap_text: str, phrase: str) -> bool:
    """Match an expected qualification with token boundaries and reviewed aliases."""
    return _contains_token_phrase(gap_text, phrase)


def _has_gap_status(text: str, phrase: str, cues: tuple[str, ...]) -> bool:
    """Require a missing/evidence cue in the same gap sentence as the qualification."""
    cue_variants = [cue.split() for cue in (_normalize_phrase(item) for item in cues)]
    cue_variants.extend([["missing"], ["absent"]])
    for clause in _gap_sentences(text):
        clause_text = " ".join(clause)
        for start, end, _variant in _phrase_spans(clause_text, phrase):
            for cue in cue_variants:
                if not cue:
                    continue
                for cue_start in range(len(clause) - len(cue) + 1):
                    cue_end = cue_start + len(cue)
                    # Keep the cue locally attached to this named qualification.
                    if cue_start >= end and cue_start - end > 8:
                        continue
                    if cue_end <= start and start - cue_end > 8:
                        continue
                    return True
    return False


def _has_local_negation(tokens: list[str], start: int, end: int) -> bool:
    nearby = tokens[max(0, start - 3) : min(len(tokens), end + 3)]
    return bool(_NEGATION_CUES & set(nearby))


def _has_positive_suffix(tokens: list[str], start: int, end: int) -> bool:
    suffixes = (
        ("is", "supported"),
        ("are", "supported"),
        ("was", "supported"),
        ("is", "demonstrated"),
        ("was", "demonstrated"),
        ("is", "proven"),
        ("was", "proven"),
        ("is", "documented"),
        ("was", "documented"),
        ("is", "shown"),
        ("was", "shown"),
        ("is", "evidenced"),
        ("was", "evidenced"),
        ("is", "listed"),
        ("was", "listed"),
        ("is", "present"),
        ("was", "present"),
        ("is", "included"),
        ("was", "included"),
    )
    following = tokens[end : end + 4]
    return any(
        following[: len(suffix)] == list(suffix)
        and not _has_local_negation(tokens, start, end + len(suffix))
        for suffix in suffixes
    )


def _has_positive_assertion(text: str, phrase: str) -> bool:
    """Catch narrow positive assertions that contradict a required gap."""
    if _has_unnegated_positive_claim(text, phrase):
        return True
    for clause in _gap_sentences(text):
        for start, end, _variant in _phrase_spans(" ".join(clause), phrase):
            if not _has_local_negation(clause, start, end) and _has_positive_suffix(
                clause, start, end
            ):
                return True
    return False


def _has_nearby_cue(
    text: str, phrase: str, cues: tuple[str, ...], *, window: int = 12
) -> bool:
    """Find an explicit lexical cue near a target phrase.

    This is deliberately a local phrase check, not semantic interpretation.
    Free-text cases still require human review for paraphrases and scope.
    """
    cue_tokens = [_normalize_phrase(cue).split() for cue in cues]
    for clause in _gap_sentences(text):
        clause_text = " ".join(clause)
        for start, end, _variant in _phrase_spans(clause_text, phrase):
            for cue in cue_tokens:
                if not cue:
                    continue
                for index in range(len(clause) - len(cue) + 1):
                    if clause[index : index + len(cue)] != cue:
                        continue
                    left = max(0, start - window)
                    right = min(len(clause), end + window)
                    if left <= index and index + len(cue) <= right:
                        following = clause[index + len(cue) : index + len(cue) + 4]
                        if set(following) & {
                            "evidence",
                            "documentation",
                            "proof",
                            "mention",
                            "support",
                        }:
                            continue
                        return True
    return False


def _has_unnegated_positive_claim(text: str, phrase: str) -> bool:
    """Catch narrow common verb-plus-phrase assertions in free text.

    This is a deterministic phrase check, not semantic entailment or a complete
    negation detector. Structured matched-skill entries are handled separately.
    """
    tokens = _normalize_phrase(text).split()
    targets = list(_phrase_variants(phrase))
    targets.extend(
        target[:-1]
        for target in tuple(targets)
        if len(target) > 1
        and target[-1] in {"certification", "certificate", "credential"}
    )
    for target_variant in targets:
        for start in range(len(tokens) - len(target_variant) + 1):
            if tokens[start : start + len(target_variant)] != target_variant:
                continue
            verb_start = max(0, start - 5)
            for index in range(verb_start, start):
                if tokens[index] not in _POSITIVE_CLAIM_VERBS:
                    continue
                between = tokens[index + 1 : start]
                before_verb = tokens[max(0, index - 6) : index]
                if (
                    set(between) <= _POSITIVE_BRIDGE_WORDS
                    and not (_NEGATION_CUES & (set(between) | set(before_verb)))
                ):
                    return True
    return False


def _strength_has_positive_phrase(text: str, phrase: str) -> bool:
    """Treat unnegated target phrases in strengths as positive evidence."""
    for clause in _clauses(text):
        for start, end, _variant in _phrase_spans(" ".join(clause), phrase):
            if not _has_local_negation(clause, start, end):
                return True
    return False


def _has_transferable_acknowledgement(text: str, phrase: str) -> bool:
    """Require a nearby, unnegated transfer cue in the target's own clause."""
    for clause in _clauses(text):
        for start, end, _variant in _phrase_spans(" ".join(clause), phrase):
            if _has_local_negation(clause, start, end):
                continue
            left = max(0, start - 6)
            right = min(len(clause), end + 6)
            for index in range(left, right):
                if clause[index] not in _TRANSFERABILITY_CUES:
                    continue
                preceding = clause[max(left, index - 3) : index]
                if not (_TRANSFERABILITY_NEGATIONS & set(preceding)):
                    return True
    return False


def _gap_status_cues(phrase: str, expected: CaseExpectations) -> tuple[str, ...]:
    normalized = _normalize_phrase(phrase)
    if normalized in {
        _normalize_phrase(item)
        for item in expected.must_describe_as_not_evidenced
    }:
        return NOT_EVIDENCED_CUES
    if normalized in {
        _normalize_phrase(item)
        for item in expected.must_describe_as_confirmed_absent
    }:
        return CONFIRMED_ABSENCE_CUES
    return (*NOT_EVIDENCED_CUES, *CONFIRMED_ABSENCE_CUES)


def _assigned_gap_expectations(
    gaps: list[str],
    expected: CaseExpectations,
    *,
    structured_positive: list[str],
    strengths: list[str],
    diagnostics: list[str],
) -> set[int]:
    """Match each expected qualification to a distinct, explicitly negative gap."""
    candidates: list[list[int]] = []
    for phrase in expected.must_identify_gaps:
        contradicted = any(
            _contains_token_phrase(item, phrase) for item in structured_positive
        )
        contradicted = contradicted or any(
            _strength_has_positive_phrase(text, phrase) for text in strengths
        )
        contradicted = contradicted or any(
            _has_positive_assertion(text, phrase) for text in diagnostics
        )
        cue_set = _gap_status_cues(phrase, expected)
        candidates.append(
            []
            if contradicted
            else [
                index
                for index, gap in enumerate(gaps)
                if _gap_phrase_is_identified(gap, phrase)
                and _has_gap_status(gap, phrase, cue_set)
            ]
        )

    gap_to_expectation: dict[int, int] = {}

    def assign(expectation_index: int, seen: set[int]) -> bool:
        for gap_index in candidates[expectation_index]:
            if gap_index in seen:
                continue
            seen.add(gap_index)
            owner = gap_to_expectation.get(gap_index)
            if owner is None or assign(owner, seen):
                gap_to_expectation[gap_index] = expectation_index
                return True
        return False

    for index in range(len(candidates)):
        assign(index, set())
    return set(gap_to_expectation.values())


def load_cases(path: Path | None = None) -> list[MatchBenchmarkCase]:
    """Load and integrity-check the checked-in synthetic case set."""
    fixture = path or Path(__file__).with_name("job_matching_cases.json")
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("benchmark fixture must be a non-empty JSON array")
    cases = [MatchBenchmarkCase.model_validate(item) for item in raw]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("benchmark case IDs must be unique")
    scenarios = [case.scenario for case in cases]
    if len(scenarios) != len(EXPECTED_SCENARIOS) or set(scenarios) != EXPECTED_SCENARIOS:
        raise ValueError(
            "benchmark fixture must contain exactly one case for each expected scenario"
        )
    known_ids = set(ids)
    for case in cases:
        if case.id in case.expectations.score_above:
            raise ValueError(f"case {case.id} cannot rank above itself")
        unknown = set(case.expectations.score_above) - known_ids
        if unknown:
            raise ValueError(
                f"case {case.id} references unknown ranking case(s): "
                f"{', '.join(sorted(unknown))}"
            )
    return cases


def build_case_prompt(case: MatchBenchmarkCase) -> str:
    """Build a case prompt with the production matching prompt builder."""
    from app.agents.job_matching.prompt import build_matching_prompt

    return build_matching_prompt(
        job_title=case.job.title,
        company_name=case.job.company_name,
        job_location=case.job.location,
        employment_type=case.job.employment_type,
        experience_level=case.job.experience_level,
        analysis=case.job.analysis,
        candidate_name=case.candidate.name,
        headline=case.candidate.headline,
        candidate_location=case.candidate.location,
        years_of_experience=case.candidate.years_of_experience,
        seniority=case.candidate.seniority,
        skills=case.candidate.skills,
        summary=case.candidate.summary,
        resume_kind=case.candidate.resume_kind,
        resume_content=case.candidate.resume_content,
    )


def evaluate_case(
    case: MatchBenchmarkCase, match: MatchResult | dict[str, Any]
) -> dict[str, Any]:
    """Check explicit field-level expectations; no semantic LLM judging."""
    result = match if isinstance(match, MatchResult) else MatchResult.model_validate(match)
    expected = case.expectations
    checks: dict[str, bool] = {}

    if expected.allowed_verdicts:
        checks["verdict_allowed"] = result.verdict in expected.allowed_verdicts

    matched = {_normalize_phrase(item) for item in result.matched_skills}
    missing = {_normalize_phrase(item) for item in result.missing_required_skills}
    missing_preferred = {
        _normalize_phrase(item) for item in result.missing_preferred_skills
    }
    for skill in expected.must_match_skills:
        checks[f"matched_skill:{skill}"] = _normalize_phrase(skill) in matched
    for skill in expected.must_be_missing_required:
        checks[f"missing_required:{skill}"] = _normalize_phrase(skill) in missing
    for skill in expected.must_be_missing_preferred:
        checks[f"missing_preferred:{skill}"] = _normalize_phrase(skill) in missing_preferred
    for skill in expected.must_not_be_missing_required:
        checks[f"not_missing_required:{skill}"] = not any(
            _contains_token_phrase(item, skill) for item in result.missing_required_skills
        )

    for field in expected.must_be_matched:
        checks[f"signal_matched:{field}"] = getattr(result, field).value == "matched"
    for field in expected.must_be_mismatched:
        checks[f"signal_mismatched:{field}"] = getattr(result, field).value == "mismatched"
    for field in expected.must_be_unclear:
        checks[f"signal_unclear:{field}"] = getattr(result, field).value == "unclear"

    for phrase in expected.must_not_claim_positive:
        checks[f"not_positive_claimed:{phrase}"] = (
            not any(
                _contains_token_phrase(item, phrase) for item in result.matched_skills
            )
            and not any(
                _strength_has_positive_phrase(item, phrase) for item in result.strengths
            )
            and not _has_positive_assertion(result.rationale, phrase)
        )

    expected_gap_indices = _assigned_gap_expectations(
        result.gaps,
        expected,
        structured_positive=result.matched_skills,
        strengths=result.strengths,
        diagnostics=result.gaps + [result.rationale],
    )
    for index, phrase in enumerate(expected.must_identify_gaps):
        checks[f"gap_identified:{phrase}"] = index in expected_gap_indices

    for phrase in expected.must_acknowledge_transferable:
        checks[f"transferable_acknowledged:{phrase}"] = any(
            _has_transferable_acknowledgement(strength, phrase)
            for strength in result.strengths
        )

    diagnostic_texts = result.gaps + [result.rationale]
    individually_named_gaps = {
        _normalize_phrase(phrase) for phrase in expected.must_identify_gaps
    }
    for phrase in expected.must_describe_as_not_evidenced:
        texts = (
            result.gaps
            if _normalize_phrase(phrase) in individually_named_gaps
            else diagnostic_texts
        )
        checks[f"not_evidenced:{phrase}"] = any(
            _gap_phrase_is_identified(text, phrase)
            and _has_nearby_cue(text, phrase, NOT_EVIDENCED_CUES)
            for text in texts
        )
    for phrase in expected.must_describe_as_confirmed_absent:
        texts = (
            result.gaps
            if _normalize_phrase(phrase) in individually_named_gaps
            else diagnostic_texts
        )
        checks[f"confirmed_absent:{phrase}"] = any(
            _gap_phrase_is_identified(text, phrase)
            and _has_nearby_cue(text, phrase, CONFIRMED_ABSENCE_CUES)
            for text in texts
        )
    for phrase in expected.must_not_describe_as_confirmed_absent:
        checks[f"not_asserted_absent:{phrase}"] = not any(
            _has_nearby_cue(text, phrase, ABSENCE_ASSERTION_CUES)
            for text in diagnostic_texts
        )

    return {
        "case_id": case.id,
        "verdict": result.verdict.value,
        "score": result.score,
        "checks": checks,
        "passed": all(checks.values()),
    }


def evaluate_benchmark(
    cases: list[MatchBenchmarkCase],
    outputs: dict[str, MatchResult | dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate per-case expectations and supported pairwise score ordering."""
    unknown_outputs = set(outputs) - {case.id for case in cases}
    if unknown_outputs:
        raise ValueError(f"outputs contain unknown case IDs: {', '.join(sorted(unknown_outputs))}")

    case_results = [
        evaluate_case(case, outputs[case.id])
        for case in cases
        if case.id in outputs
    ]
    normalized_outputs = {
        case_id: value
        if isinstance(value, MatchResult)
        else MatchResult.model_validate(value)
        for case_id, value in outputs.items()
    }
    ranking_checks: list[dict[str, Any]] = []
    for case in cases:
        for lower_id in case.expectations.score_above:
            if case.id not in normalized_outputs or lower_id not in normalized_outputs:
                ranking_checks.append(
                    {
                        "higher_case": case.id,
                        "lower_case": lower_id,
                        "status": "not_run",
                        "passed": False,
                    }
                )
                continue
            higher_score = normalized_outputs[case.id].score
            lower_score = normalized_outputs[lower_id].score
            ranking_checks.append(
                {
                    "higher_case": case.id,
                    "lower_case": lower_id,
                    "higher_score": higher_score,
                    "lower_score": lower_score,
                    "status": "checked",
                    "passed": higher_score > lower_score,
                }
            )

    missing_case_ids = [case.id for case in cases if case.id not in normalized_outputs]
    complete = not missing_case_ids
    passed = (
        complete
        and all(row["passed"] for row in case_results)
        and all(row["passed"] for row in ranking_checks)
    )
    return {
        "complete": complete,
        "passed": passed,
        "missing_case_ids": missing_case_ids,
        "case_results": case_results,
        "ranking_checks": ranking_checks,
        "human_review_required": [
            "Review free-text claims and evidence scope; lexical checks cannot prove semantic grounding.",
            "Review score/verdict effects for unknown or absent mandatory qualifications; no policy or absolute score bands are defined.",
        ],
    }
