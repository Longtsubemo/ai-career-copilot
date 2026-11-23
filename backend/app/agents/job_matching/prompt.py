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

PROMPT_VERSION = "job-matching-v5"

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
- Assess education only against an education requirement in the job
  evidence. When a stated requirement is supported by candidate evidence,
  use "matched"; when the requirement exists but the evidence is insufficient,
  use "unclear". If no education requirement and no candidate education
  evidence are supplied, use "unclear" because the schema has no
  "not_applicable" value. Never infer a degree, qualification, or education
  history from unrelated profile details.
- Distinguish an unmentioned qualification from an explicitly absent
  one. Describe an unmentioned skill, certification, or other
  qualification as "not evidenced", "not listed", or needing
  verification; do not claim the candidate lacks it.
- For a required or mandatory qualification, an omission means its
  status is unknown: do not claim it is fulfilled or confirmed absent.
  Identify the evidence gap. State that a qualification is absent or
  not held only when the supplied candidate evidence explicitly says so.
- Assess experience separately from skill signals. For a generic minimum
  total-years requirement, compare documented total professional tenure;
  when it meets or exceeds the minimum, use "matched" even if a required
  technical skill is not evidenced. For an explicit years-in-role, domain,
  or technology requirement, assess only experience supported as relevant;
  unrelated tenure does not satisfy it. Do not turn an unrelated skill gap
  into an experience mismatch. Use "unclear" when duration or relevance
  cannot be established, and "mismatched" only when documented experience
  clearly falls short of the stated requirement.
- Evaluate required skills more heavily than preferred skills. A
  missing required skill is a major gap; a missing preferred skill is
  a minor one.
- When "requirement_items" is non-empty, use each item's structured status
  as the authoritative requirement classification: "required" is explicitly
  mandatory, "preferred" is desirable but not mandatory, and "unclear" must
  not be promoted to either required or preferred. Use the item's associated
  evidence as context, but do not let contradictory legacy arrays override
  its structured status. For a required structured skill, use
  "missing_required_skills" when candidate evidence does not establish it;
  for a preferred structured skill, use "missing_preferred_skills" only when
  useful; an unclear structured skill belongs in neither missing-skills
  field. For each unsupported required qualification or certification, add
  its own specific entry to "gaps". Preferred or unclear qualifications and
  certifications are not mandatory gaps.
- In structured mode, legacy skill-array entries not represented by a
  requirement item retain their legacy status: "required_skills" means
  required and "preferred_skills" means preferred. If the same legacy-only
  skill appears in both arrays, treat its status as unclear and put it in
  neither missing-skills field. For qualifications or certifications not
  represented by a structured item, a generic legacy-list mention alone does
  not establish mandatory status; require explicit mandatory wording or
  context. Do not infer status from a list heading.
- When "requirement_items" is absent or empty, preserve the v4 legacy
  behavior: items in "required_skills" are required and items in
  "preferred_skills" are preferred; generic qualifications and certifications
  are not mandatory solely because of their field, and their explicit wording
  or job-analysis context must establish any mandatory status. Preserve
  uncertainty when status is unclear. Do not assume every item in a generic
  "qualifications" or "certifications" list is mandatory. An item marked
  preferred or nice-to-have remains preferred. If a listed qualification's
  status is unclear, preserve that uncertainty; when relevant, name it
  individually in "gaps" as unclear without treating it as essential.
- For each explicitly mandatory qualification that candidate evidence does
  not establish as met, add a separate, specific entry to "gaps" that names
  the qualification. When evidence is silent, say it is not evidenced, not
  demonstrated, or needs verification; say it is absent or not held only
  when the candidate evidence explicitly establishes that. Do not collapse
  distinct qualifications into a generic statement such as "lacks relevant
  experience". Continue to list missing required skills in
  "missing_required_skills". Do not present preferred qualifications as
  mandatory gaps or weigh them like essential requirements; when useful,
  identify a preferred skill in "missing_preferred_skills" and describe it
  only as a minor consideration.
- Do not treat related but distinct capabilities as proof of one another.
  For example, GPU inference is not GPU training; model serving is not model
  training or fine-tuning; distributed inference is not distributed training;
  and model deployment is not model development. When supported by the
  evidence, acknowledge related work as transferable experience, but do not
  say it satisfies the specific requirement unless that capability is
  evidenced. Apply this distinction to the capability actually named for any
  role; these examples do not make AI or GPU experience a universal
  requirement.
- Treat skills as equivalent when they name the same capability (for
  example "js" and "javascript"), but never stretch a skill into an
  unrelated one.
- Ground every important claim — matched and missing skills, strengths,
  gaps, and the rationale — in the candidate profile, the resume, or
  the job analysis supplied to you.
- Distinguish lack of evidence from evidence of absence throughout the
  rationale and gap descriptions. If a skill or qualification is not
  mentioned or the evidence is insufficient, describe it as "not evidenced",
  "not demonstrated in the supplied resume", or needing verification. Use
  "does not have" or "lacks" only when supplied evidence explicitly
  establishes absence. Still identify unsupported requirements and explain
  why they matter to the match.
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
    requirement_items = analysis.get("requirement_items")
    if isinstance(requirement_items, Sequence) and not isinstance(
        requirement_items, (str, bytes)
    ) and requirement_items:
        lines.extend(
            [
                "",
                "REQUIREMENT STATUS POLICY: STRUCTURED ITEMS PRESENT",
                "Use requirement_items as the authoritative status for each "
                "represented item. required is mandatory; preferred is "
                "desirable but not mandatory; unclear remains uncertain and "
                "must not be placed in either missing-skills list. Evidence is "
                "context, not permission to override status. Structured status "
                "overrides contradictory legacy arrays for the same item.",
                "For legacy-only skills absent from requirement_items, use "
                "required_skills/preferred_skills as before, except a skill in "
                "both arrays is unclear and belongs in neither missing list.",
                "Do not make generic legacy qualifications or certifications "
                "mandatory unless their own wording or explicit context says "
                "they are required. Name each unsupported required non-skill "
                "item separately in gaps.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "REQUIREMENT STATUS POLICY: LEGACY ANALYSIS",
                "No structured requirement items are available. Use the "
                "existing v4 behavior. Preserve the v4 legacy behavior: "
                "required_skills are required, "
                "preferred_skills are preferred, and a generic qualification "
                "or certification list does not by itself make an item "
                "mandatory. Preserve uncertainty when status is not explicit.",
            ]
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
