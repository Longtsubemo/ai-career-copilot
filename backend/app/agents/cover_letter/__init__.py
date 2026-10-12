"""Cover Letter agent — grounded cover letter generation via Gemini."""

from app.agents.cover_letter.agent import (
    CoverLetterAgent,
    persist_cover_letter,
    register_cover_letter_handler,
)
from app.agents.cover_letter.contract import (
    COVER_LETTER_AGENT_NAME,
    CandidateNotFoundError,
    CoverLetterEnvelope,
    CoverLetterInputError,
    CoverLetterRequest,
    CoverLetterResult,
    InvalidMasterResumeError,
    InvalidMatchError,
    JobAnalysisNotFoundError,
    JobNotFoundError,
    MasterResumeNotFoundError,
    MatchNotFoundError,
    cover_letter_idempotency_key,
)
from app.agents.cover_letter.prompt import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    build_cover_letter_prompt,
)

__all__ = [
    "COVER_LETTER_AGENT_NAME",
    "PROMPT_VERSION",
    "SYSTEM_INSTRUCTION",
    "CandidateNotFoundError",
    "CoverLetterAgent",
    "CoverLetterEnvelope",
    "CoverLetterInputError",
    "CoverLetterRequest",
    "CoverLetterResult",
    "InvalidMasterResumeError",
    "InvalidMatchError",
    "JobAnalysisNotFoundError",
    "JobNotFoundError",
    "MasterResumeNotFoundError",
    "MatchNotFoundError",
    "build_cover_letter_prompt",
    "cover_letter_idempotency_key",
    "persist_cover_letter",
    "register_cover_letter_handler",
]
