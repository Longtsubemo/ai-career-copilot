"""Versioned prompt for the Job Matching agent.

``PROMPT_VERSION`` is persisted with every match, is part of the AgentRun
idempotency key and is half of the ``matches`` unique scope. Any change to
the system instruction or to :func:`build_matching_prompt` is a behavior
change and MUST bump ``PROMPT_VERSION`` — silently editing a prompt under
the same version would let different assessments claim to be the same
prompt version.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

PROMPT_VERSION = "job-matching-v2"

SYSTEM_INSTRUCTION = """\
You are a candidate-job matching evaluator. You receive two blocks of
evidence: a JOB EVIDENCE block (a persisted structured analysis of the
job plus its key metadata) and a CANDIDATE EVIDENCE block (the
candidate's profile and one resume document). Return a structured
assessment of how well this candidate fits this job as JSON matching
the provided schema.

Rules:
- Use ONLY the supplied evidence. Never invent experience, employers,
  education, credentials, technologies, or skills.
- Absence of information is not evidence of a mismatch: when the
  evidence does not address something, report it as "unclear" instead
  of guessing a negative.
- Distinguish an unmentioned qualification from an explicitly absent
  one. Describe an unmentioned skill, certification, or other
  qualification as "not evidenced", "not listed", or needing
  verification; do not claim the candidate lacks it.
- For a required or mandatory qualification, an omission means its
  status is unknown: do not claim it is fulfilled or confirmed absent.
  Identify the evidence gap. State that a qualification is absent or
  not held only when the supplied candidate evidence explicitly says so.
- Judge experience against the job's stated requirement. For a generic
  total-years requirement, compare total professional tenure. For an
  explicitly relevant-role requirement, use only experience supported
  as relevant; unrelated tenure does not satisfy it. Use "mismatched"
  only when the evidence establishes the requirement is not met, and
  "unclear" when dates or role relevance cannot be established.
- Evaluate required skills more heavily than preferred skills. A
  missing required skill is a major gap; a missing preferred skill is
  a minor one.
- Treat skills as equivalent when they name the same capability (for
  example "js" and "javascript"), but never stretch a skill into an
  unrelated one.
- Ground every important claim — matched and missing skills, strengths,
  gaps, and the rationale — in the candidate profile, the resume, or
  the job analysis supplied to you.
- Distinguish explicit evidence from uncertainty and explain major gaps
  plainly.
- "score" is the candidate's overall fit for this job on a 0-100 scale.
- "verdict" must be one of the vocabulary values in the schema, and
  each *_match field must be "matched", "mismatched" or "unclear".
- "confidence" reflects how well your assessment is supported by the
  evidence (0.0 to 1.0).
- Return only the JSON object; no prose, no markdown fences.
"""


def build_matching_prompt(
    *,
    job_title: str,
    analysis: Mapping[str, object],
    candidate_name: str,
    resume_kind: str,
    resume_content: str,
    company_name: str | None = None,
    job_location: str | None = None,
    employment_type: str | None = None,
    experience_level: str | None = None,
    headline: str | None = None,
    candidate_location: str | None = None,
    years_of_experience: float | None = None,
    seniority: str | None = None,
    skills: Sequence[str] | None = None,
    summary: str | None = None,
) -> str:
    """Compose the deterministic user prompt for one matching computation.

    Job evidence (metadata + the persisted analysis) and candidate
    evidence (profile + the selected resume) are kept in separate,
    labelled blocks. Optional fields are included only when present, and
    the analysis is dumped with sorted keys, so the same inputs always
    produce a byte-identical prompt. Raw job internals — description
    text, URLs, crawl state — are deliberately excluded: the structured
    analysis is the job side of this comparison.
    """
    lines = [
        "Evaluate how well the candidate matches the job using only the "
        "evidence below.",
        "",
        "JOB EVIDENCE",
    ]
    if company_name:
        lines.append(f"Company: {company_name}")
    lines.append(f"Title: {job_title}")
    if job_location:
        lines.append(f"Location: {job_location}")
    if employment_type:
        lines.append(f"Employment type: {employment_type}")
    if experience_level:
        lines.append(f"Experience level: {experience_level}")
    lines.append("Structured job analysis:")
    lines.append(
        json.dumps(dict(analysis), indent=2, sort_keys=True, ensure_ascii=False)
    )
    lines.extend(
        [
            "",
            "CANDIDATE EVIDENCE",
            f"Name: {candidate_name}",
        ]
    )
    if headline:
        lines.append(f"Headline: {headline}")
    if candidate_location:
        lines.append(f"Location: {candidate_location}")
    if years_of_experience is not None:
        lines.append(f"Years of experience: {years_of_experience:g}")
    if seniority:
        lines.append(f"Seniority: {seniority}")
    if skills:
        lines.append(f"Skills: {', '.join(skills)}")
    if summary:
        lines.append("Profile summary:")
        lines.append(summary.strip())
    lines.append(f"Resume document ({resume_kind}):")
    lines.append(resume_content.strip())
    return "\n".join(lines)
