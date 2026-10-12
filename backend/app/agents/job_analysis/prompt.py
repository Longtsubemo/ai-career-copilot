"""Versioned prompt for the Job Analysis agent.

``PROMPT_VERSION`` is persisted with every analysis and is part of the
AgentRun idempotency key. Any change to the system instruction or to
:func:`build_analysis_prompt` is a behavior change and MUST bump
``PROMPT_VERSION`` — silently editing a prompt under the same version
would let two different analyses claim to be ``job-analysis-v1``.
"""

from __future__ import annotations

PROMPT_VERSION = "job-analysis-v1"

SYSTEM_INSTRUCTION = """\
You are a job-posting analyst. You receive one job posting and must return
a structured analysis of it as JSON matching the provided schema.

Rules:
- Analyze ONLY the supplied posting. Do not use outside knowledge about
  the company or the market, and do not consider any candidate.
- Extract requirements that are explicitly stated. Distinguish skills the
  posting requires from skills it merely prefers.
- Never invent requirements, technologies, certifications, or numbers
  that the posting does not state.
- When information is absent, use an empty list or null — not guesses.
- Put anything unclear, contradictory, or missing into "ambiguities"
  instead of resolving it speculatively.
- "confidence" reflects how much of your analysis the posting text
  directly supports (0.0 to 1.0).
- Return only the JSON object; no prose, no markdown fences.
"""


def build_analysis_prompt(
    *,
    title: str,
    description: str,
    company: str | None = None,
    location: str | None = None,
    employment_type: str | None = None,
    experience_level: str | None = None,
) -> str:
    """Compose the deterministic user prompt for one job posting.

    Optional fields are included only when present, so two jobs with the
    same content always produce byte-identical prompts.
    """
    lines = ["Job posting to analyze:", f"Title: {title}"]
    if company:
        lines.append(f"Company: {company}")
    if location:
        lines.append(f"Location: {location}")
    if employment_type:
        lines.append(f"Employment type: {employment_type}")
    if experience_level:
        lines.append(f"Experience level: {experience_level}")
    lines.append("Description:")
    lines.append(description.strip())
    return "\n".join(lines)
