"""Job Analysis agent — semantic analysis of persisted jobs via Gemini."""

from app.agents.job_analysis.agent import (
    JobAnalysisAgent,
    register_job_analysis_handler,
)
from app.agents.job_analysis.contract import (
    JOB_ANALYSIS_AGENT_NAME,
    InsufficientJobContentError,
    JobRequirement,
    JobAnalysisInputError,
    JobAnalysisRequest,
    JobAnalysisResult,
    JobAnalysisResponse,
    JobNotFoundError,
    RequirementCategory,
    RequirementStatus,
    job_analysis_idempotency_key,
)
from app.agents.job_analysis.prompt import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    build_analysis_prompt,
)

__all__ = [
    "JOB_ANALYSIS_AGENT_NAME",
    "PROMPT_VERSION",
    "SYSTEM_INSTRUCTION",
    "InsufficientJobContentError",
    "JobRequirement",
    "JobAnalysisAgent",
    "JobAnalysisInputError",
    "JobAnalysisRequest",
    "JobAnalysisResult",
    "JobAnalysisResponse",
    "JobNotFoundError",
    "RequirementCategory",
    "RequirementStatus",
    "build_analysis_prompt",
    "job_analysis_idempotency_key",
    "register_job_analysis_handler",
]
