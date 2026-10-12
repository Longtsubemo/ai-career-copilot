"""Job Matching agent — candidate/job fit evaluation via Gemini."""

from app.agents.job_matching.agent import (
    JobMatchingAgent,
    persist_match,
    register_job_matching_handler,
)
from app.agents.job_matching.contract import (
    JOB_MATCHING_AGENT_NAME,
    CandidateNotFoundError,
    InsufficientCandidateProfileError,
    InvalidResumeDocumentError,
    JobAnalysisNotFoundError,
    JobMatchingInputError,
    JobMatchingRequest,
    JobNotFoundError,
    MatchEnvelope,
    MatchResult,
    MatchSignal,
    MatchVerdict,
    ResumeDocumentNotFoundError,
    job_matching_idempotency_key,
)
from app.agents.job_matching.prompt import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    build_matching_prompt,
)

__all__ = [
    "JOB_MATCHING_AGENT_NAME",
    "PROMPT_VERSION",
    "SYSTEM_INSTRUCTION",
    "CandidateNotFoundError",
    "InsufficientCandidateProfileError",
    "InvalidResumeDocumentError",
    "JobAnalysisNotFoundError",
    "JobMatchingAgent",
    "JobMatchingInputError",
    "JobMatchingRequest",
    "JobNotFoundError",
    "MatchEnvelope",
    "MatchResult",
    "MatchSignal",
    "MatchVerdict",
    "ResumeDocumentNotFoundError",
    "build_matching_prompt",
    "job_matching_idempotency_key",
    "persist_match",
    "register_job_matching_handler",
]
