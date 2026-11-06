"""Structured input/output contract of the Job Analysis agent.

The input is a job reference; the output is a Pydantic-validated semantic
analysis produced by Gemini. :class:`JobAnalysisResponse` is the exact
schema handed to Gemini as its structured-output schema. The base
:class:`JobAnalysisResult` remains able to read historical stored JSON that
predates structured requirement items. LLM output is data only — none of
these types participates in runtime state transitions.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
import re
import unicodedata
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

JOB_ANALYSIS_AGENT_NAME = "job_analysis"

_REQUIRED_CUE = re.compile(
    r"\b(?:required|mandatory|essential|necessary|needed|must(?:\s+"
    r"(?:have|hold|possess|obtain|complete))?|minimum(?:\s+requirement)?|"
    r"at\s+least|no\s+fewer\s+than)\b"
)
_PREFERRED_CUE = re.compile(
    r"\b(?:preferred|preferable|desirable|advantageous)\b|"
    r"\ba\s+plus\b|\bnice[- ]to[- ]have\b"
)
_CUE_NEGATION = re.compile(
    r"\b(?:not|never|isn't|aren't|wasn't|weren't|no\s+longer)"
    r"(?:\s+\w+){0,3}\s*$"
)
_STATUS_CONTINUATION_WORDS = {
    "a",
    "active",
    "an",
    "and",
    "also",
    "are",
    "as",
    "be",
    "been",
    "but",
    "complete",
    "have",
    "hold",
    "is",
    "longer",
    "must",
    "never",
    "no",
    "not",
    "obtain",
    "only",
    "possess",
    "required",
    "should",
    "still",
    "the",
    "to",
    "was",
    "were",
}


def _normalize_requirement_text(value: str) -> str:
    """Normalize identity/evidence text without discarding punctuation."""
    value = unicodedata.normalize("NFC", value).casefold()
    return " ".join(unicodedata.normalize("NFC", value).split())


def _contains_item(text: str, item: str) -> bool:
    normalized_item = _normalize_requirement_text(item)
    if not normalized_item:
        return False
    pattern = re.compile(rf"(?<!\w){re.escape(normalized_item)}(?!\w)")
    return pattern.search(_normalize_requirement_text(text)) is not None


def _continuation_only(text: str) -> bool:
    """Whether text between cues contains only connector/status grammar."""
    words = re.findall(r"\b[\w'-]+\b", _normalize_requirement_text(text))
    return all(word in _STATUS_CONTINUATION_WORDS for word in words)


def _cue_is_associated_with_item(
    segment: str,
    item: str,
    cue: re.Match[str],
    all_cues: list[re.Match[str]],
) -> bool:
    """Associate a cue with the item, not a neighboring requirement clause.

    A trailing cue can apply to a coordinated item list when no prior status
    cue intervenes (for example, "AWS and Azure certifications are required").
    After an earlier cue, only connector/auxiliary wording may carry the
    following cue forward to the same item. A new item name ends that scope.
    """
    normalized = _normalize_requirement_text(segment)
    normalized_item = _normalize_requirement_text(item)
    item_pattern = re.compile(rf"(?<!\w){re.escape(normalized_item)}(?!\w)")
    item_matches = list(item_pattern.finditer(normalized))
    if not item_matches:
        return False

    preceding_items = [match for match in item_matches if match.end() <= cue.start()]
    if preceding_items:
        item_match = preceding_items[-1]
        intervening_cues = [
            other
            for other in all_cues
            if item_match.end() <= other.start() < cue.start()
        ]
        if not intervening_cues:
            return True
        last_cue = intervening_cues[-1]
        gap = normalized[last_cue.end() : cue.start()]
        if item_pattern.search(gap) or _continuation_only(gap):
            return True
        return False

    following_items = [match for match in item_matches if match.start() >= cue.end()]
    if not following_items:
        return False
    item_match = following_items[0]
    intervening_cues = [
        other
        for other in all_cues
        if cue.end() <= other.start() < item_match.start()
    ]
    gap = normalized[cue.end() : item_match.start()]
    gap_words = re.findall(r"\b[\w'-]+\b", gap)
    return (
        not intervening_cues
        and not (gap_words and gap_words[-1] in {"and", "but"})
        and _continuation_only(gap)
    )


def _cue_is_negated(
    segment: str,
    item: str,
    cue: re.Match[str],
    pattern: re.Pattern[str],
) -> bool:
    normalized = _normalize_requirement_text(segment)
    prefix = normalized[: cue.start()]
    if re.search(r"\bnot\s+only\s*$", prefix):
        return False

    # Keep negation local to its cue; a coordinating contrast can introduce a
    # separate positive cue for the same item.
    negation_scope = re.split(r"\b(?:but|and)\b", prefix)[-1]
    if _CUE_NEGATION.search(negation_scope):
        return True
    if pattern is _REQUIRED_CUE and normalized[cue.start() :].startswith("must not "):
        return True

    if pattern is _REQUIRED_CUE:
        normalized_item = _normalize_requirement_text(item)
        item_pattern = re.compile(rf"(?<!\w){re.escape(normalized_item)}(?!\w)")
        item_matches = [match for match in item_pattern.finditer(prefix)]
        if item_matches:
            item_match = item_matches[-1]
            before_item = prefix[: item_match.start()].rstrip()
            between_item_and_cue = prefix[item_match.end() :].strip()
            if (
                re.search(r"\bno\s*$", before_item)
                and re.fullmatch(
                    r"(?:(?:is|are|was|were|should\s+be)"
                    r"(?:\s+\w+){0,2})?",
                    between_item_and_cue,
                )
            ):
                return True
    return False


def _evidence_signals(item: str, excerpts: list[str]) -> tuple[bool, bool, bool]:
    """Return associated positive required/preferred and negative-required cues."""
    required = False
    preferred = False
    negated_required = False
    for excerpt in excerpts:
        # A cue in a neighboring sentence must not classify this item. Keep
        # punctuation in the segments so clauses remain distinguishable.
        segments = re.split(r"(?<=[.!?;])\s+|\r?\n+", excerpt)
        for segment in segments:
            if not _contains_item(segment, item):
                continue
            cues = sorted(
                [
                    *list(_REQUIRED_CUE.finditer(segment)),
                    *list(_PREFERRED_CUE.finditer(segment)),
                ],
                key=lambda match: match.start(),
            )
            for pattern in (_REQUIRED_CUE, _PREFERRED_CUE):
                for cue in pattern.finditer(segment):
                    if not _cue_is_associated_with_item(segment, item, cue, cues):
                        continue
                    negated = _cue_is_negated(segment, item, cue, pattern)
                    if pattern is _REQUIRED_CUE:
                        if negated:
                            negated_required = True
                        else:
                            required = True
                    elif not negated:
                        preferred = True
    return required, preferred, negated_required


class RequirementCategory(StrEnum):
    """Kind of posting requirement represented by a structured item."""

    skill = "skill"
    qualification = "qualification"
    certification = "certification"


class RequirementStatus(StrEnum):
    """Posting-supported classification, not an inference from list membership."""

    required = "required"
    preferred = "preferred"
    unclear = "unclear"


class JobRequirement(BaseModel):
    """One independently classified posting requirement and its source text."""

    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1, description="Concise name of one requirement.")
    category: RequirementCategory
    status: RequirementStatus
    evidence: list[str] = Field(
        min_length=1,
        description="One or more short, verbatim excerpts from the job posting.",
    )

    @field_validator("item")
    @classmethod
    def _item_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("item must not be blank")
        return value

    @field_validator("evidence")
    @classmethod
    def _evidence_must_contain_nonblank_excerpts(cls, value: list[str]) -> list[str]:
        excerpts = [excerpt.strip() for excerpt in value]
        if not excerpts or any(not excerpt for excerpt in excerpts):
            raise ValueError("evidence must contain nonblank source excerpts")
        normalized = [_normalize_requirement_text(excerpt) for excerpt in excerpts]
        if len(normalized) != len(set(normalized)):
            raise ValueError("evidence excerpts must not be duplicated")
        return excerpts

    @model_validator(mode="after")
    def _item_and_status_are_supported_by_evidence(self) -> JobRequirement:
        if any(not _contains_item(excerpt, self.item) for excerpt in self.evidence):
            raise ValueError("each evidence excerpt must name its requirement item")

        has_required, has_preferred, has_negated_required = _evidence_signals(
            self.item, self.evidence
        )
        if self.status is RequirementStatus.required:
            if has_preferred:
                raise ValueError(
                    "conflicting required/preferred evidence must use unclear status"
                )
            if has_required and has_negated_required:
                raise ValueError(
                    "conflicting required/negated-required evidence must use unclear status"
                )
            if not has_required:
                raise ValueError(
                    "required status needs an associated explicit mandatory cue"
                )
        elif self.status is RequirementStatus.preferred:
            if has_required:
                raise ValueError(
                    "conflicting required/preferred evidence must use unclear status"
                )
            if not has_preferred:
                raise ValueError(
                    "preferred status needs an associated explicit preference cue"
                )
        return self

    def validate_evidence_against(self, source_text: str) -> None:
        """Reject excerpts that are not present in the supplied posting.

        Whitespace and letter casing are normalized to tolerate formatting
        changes while still requiring the evidence wording to come from the
        source. This is a provenance check, not a semantic-status check.
        """
        normalized_source = _normalize_requirement_text(source_text)
        for excerpt in self.evidence:
            normalized_excerpt = _normalize_requirement_text(excerpt)
            if normalized_excerpt not in normalized_source:
                raise ValueError(
                    "requirement evidence is not present in the supplied job description"
                )


class JobAnalysisInputError(ValueError):
    """The job analysis request failed deterministic validation."""


class JobNotFoundError(LookupError):
    """The requested job id does not exist in the jobs table."""


class InsufficientJobContentError(ValueError):
    """The job exists but carries no content worth sending to Gemini."""


class JobAnalysisRequest(BaseModel):
    """A job analysis request: exactly one required ``job_id``."""

    model_config = ConfigDict(extra="forbid")

    job_id: UUID

    @field_validator("job_id")
    @classmethod
    def _reject_nil_uuid(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("job_id must not be the nil UUID")
        return value

    @classmethod
    def from_payload(cls, payload: object) -> JobAnalysisRequest:
        """Validate a raw handler payload, raising :class:`JobAnalysisInputError`."""
        if not isinstance(payload, Mapping):
            raise JobAnalysisInputError(
                f"job analysis input must be a mapping, got {type(payload).__name__}"
            )
        try:
            return cls.model_validate(dict(payload))
        except ValidationError as exc:
            raise JobAnalysisInputError(str(exc)) from exc


class JobAnalysisResult(BaseModel):
    """Semantic analysis of a single job posting.

    Deliberately excludes fields the ``jobs`` table already stores
    deterministically (title, location, employment type, company name);
    this schema only carries what Gemini infers or extracts from the
    posting text. Nullable numeric ranges and defaulted lists mean the
    absence of information is representable without inventing values.
    """

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, description="Concise factual summary of the role.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="How well-supported this analysis is by the posting."
    )

    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)
    requirement_items: list[JobRequirement] = Field(
        default_factory=list,
        description=(
            "Per-item status and source evidence for skills, qualifications, "
            "and certifications. Legacy category lists remain for compatibility."
        ),
    )

    experience_years_min: int | None = Field(default=None, ge=0)
    experience_years_max: int | None = Field(default=None, ge=0)

    seniority: str | None = None
    remote_type: str | None = None

    domain_keywords: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    education_requirements: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(
        default_factory=list,
        description="Ambiguities or gaps observed in the posting, never guessed away.",
    )

    @model_validator(mode="after")
    def _experience_range_is_ordered(self) -> JobAnalysisResult:
        if (
            self.experience_years_min is not None
            and self.experience_years_max is not None
            and self.experience_years_min > self.experience_years_max
        ):
            raise ValueError(
                "experience_years_min must not exceed experience_years_max"
            )
        return self

    def validate_requirement_evidence(self, source_text: str) -> None:
        """Validate every structured requirement excerpt against its source."""
        for requirement in self.requirement_items:
            requirement.validate_evidence_against(source_text)


class JobAnalysisResponse(JobAnalysisResult):
    """Strict schema used for new model output.

    ``JobAnalysisResult`` keeps a default for reading historical JSONB payloads;
    new Gemini responses must include the structured status field.
    """

    requirement_items: list[JobRequirement] = Field(
        description="Per-item status and source evidence for posting requirements."
    )

    @model_validator(mode="after")
    def _requirement_items_are_unique_and_skill_lists_consistent(
        self,
    ) -> JobAnalysisResponse:
        seen: set[tuple[RequirementCategory, str]] = set()
        for requirement in self.requirement_items:
            identity = (
                requirement.category,
                _normalize_requirement_text(requirement.item),
            )
            if identity in seen:
                raise ValueError(
                    "duplicate requirement item; combine conflicting source "
                    "evidence into one unclear item"
                )
            seen.add(identity)

        required = [_normalize_requirement_text(skill) for skill in self.required_skills]
        preferred = [
            _normalize_requirement_text(skill) for skill in self.preferred_skills
        ]
        required_set = set(required)
        preferred_set = set(preferred)
        if len(required) != len(required_set) or len(preferred) != len(preferred_set):
            raise ValueError("legacy skill arrays must not contain duplicate items")
        if required_set & preferred_set:
            raise ValueError("a skill must not appear in both legacy skill arrays")

        for requirement in self.requirement_items:
            if requirement.category is not RequirementCategory.skill:
                continue
            identity = _normalize_requirement_text(requirement.item)
            if requirement.status is RequirementStatus.required and identity not in required_set:
                raise ValueError(
                    "structured required skills must appear in required_skills"
                )
            if requirement.status is RequirementStatus.preferred and identity not in preferred_set:
                raise ValueError(
                    "structured preferred skills must appear in preferred_skills"
                )
            if requirement.status is RequirementStatus.unclear and identity in (
                required_set | preferred_set
            ):
                raise ValueError(
                    "structured unclear skills must not appear in legacy skill arrays"
                )
        return self


def job_analysis_idempotency_key(job_id: UUID | str, prompt_version: str) -> str:
    """Return the deterministic idempotency key for a job analysis run.

    ``job_analysis:{job_id}:{prompt_version}`` — same job and prompt
    version always collapse onto one run, a new prompt version always
    gets a fresh one.
    """
    return f"job_analysis:{job_id}:{prompt_version}"
