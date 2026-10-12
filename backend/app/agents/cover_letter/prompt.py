"""Versioned prompt for the Cover Letter agent.

``PROMPT_VERSION`` is persisted with every generated letter, is part of
the AgentRun idempotency key and is half of what makes a generation
reproducible. Any change to the system instruction or to
:func:`build_cover_letter_prompt` is a behavior change and MUST bump
``PROMPT_VERSION`` — silently editing a prompt under the same version
would let two different letters claim to be ``cover-letter-v1``.

The prompt hands Gemini three blocks of persisted evidence and nothing
else: the job side (deterministic job columns plus the structured
JobAnalysis) and the candidate side (profile, the uploaded master resume
and the Match). Raw job internals — description text, URLs, crawl state
— are deliberately excluded: the structured analysis is the authoritative
job evidence, and the raw posting would invite the model to re-derive
requirements the analysis already settled.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from app.agents.job_matching.contract import MatchResult

PROMPT_VERSION = "cover-letter-v1"

SYSTEM_INSTRUCTION = """\
You are a professional cover letter writer. You receive two blocks of
evidence: a JOB EVIDENCE block (a persisted structured analysis of the
job plus its key metadata) and a CANDIDATE EVIDENCE block (the
candidate's profile, their uploaded master resume, and a persisted match
assessment). Write one cover letter for this specific job as JSON
matching the provided schema.

Evidence authority:
- The master resume is the authoritative source of the candidate's
  experience.
- The structured job analysis is the authoritative description of the
  job and what it requires.
- The match assessment is the authoritative comparison: it names the
  genuine strengths worth emphasising and the known gaps.

Rules:
- Use ONLY the supplied evidence. Never fabricate employers, job titles,
  dates, achievements, metrics, technologies, projects, education,
  certifications, awards or responsibilities.
- Never claim a missing required skill, or a missing preferred skill, as
  experience the candidate possesses.
- Never turn a "nice to have" into a fact about the candidate.
- If information is uncertain or absent from the evidence, omit it or
  phrase it conservatively instead of inventing it.
- Be specific to this job: address its requirements and draw on the
  relevant experience in the resume. Do not restate the whole resume and
  do not pad the letter with generic filler.
- Keep the letter professional and natural: a greeting where
  appropriate, an opening, body paragraphs, a closing and a sign-off.
- "content" is the full cover letter text; "confidence" (0.0 to 1.0)
  reflects how well the letter is grounded in the supplied evidence.
- Return only the JSON object; no prose, no markdown fences.
"""


def build_cover_letter_prompt(
    *,
    job_title: str,
    analysis: Mapping[str, object],
    candidate_name: str,
    match: MatchResult,
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
    """Compose the deterministic user prompt for one generation.

    Job evidence (metadata + the persisted analysis) and candidate
    evidence (profile + the uploaded master resume + the match) are kept
    in separate, labelled blocks. Optional fields are included only when
    present and the analysis is dumped with sorted keys, so the same
    inputs always produce a byte-identical prompt. Of the stored match
    only the decision-relevant portions are rendered — score, verdict,
    matched and missing skills, strengths, gaps, rationale and
    confidence — never its internal identifiers or signal plumbing.
    """
    lines = [
        "Write a cover letter for this job using only the evidence below.",
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
    lines.append("Master resume:")
    lines.append(resume_content.strip())
    lines.append("Match assessment:")
    lines.append(f"Fit score: {match.score:g}")
    lines.append(f"Verdict: {match.verdict.value}")
    lines.append(f"Matched skills: {_or_none(match.matched_skills)}")
    lines.append(f"Missing required skills: {_or_none(match.missing_required_skills)}")
    lines.append(f"Missing preferred skills: {_or_none(match.missing_preferred_skills)}")
    lines.extend(_bulleted("Strengths", match.strengths))
    lines.extend(_bulleted("Gaps", match.gaps))
    lines.append(f"Rationale: {match.rationale}")
    lines.append(f"Match confidence: {match.confidence:g}")
    return "\n".join(lines)


def _or_none(items: Sequence[str]) -> str:
    return ", ".join(items) if items else "none"


def _bulleted(label: str, items: Sequence[str]) -> list[str]:
    if not items:
        return [f"{label}: none"]
    return [f"{label}:"] + [f"- {item}" for item in items]
