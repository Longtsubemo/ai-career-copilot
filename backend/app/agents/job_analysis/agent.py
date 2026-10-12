"""Job Analysis agent: load a job, analyze it with Gemini, persist the result.

The agent executes through the generic :class:`AgentRuntime` as the
``job_analysis`` handler and never influences runtime state transitions:
every Gemini outcome is data or an exception, and only the runtime decides
retry versus failure.

Execution order keeps database work away from the Gemini API call:

* look up an existing successful analysis (short-circuit, no Gemini call);
* load the job and its company (reads only);
* build the versioned prompt and call Gemini — no database work;
* validate the structured result (done at the client boundary);
* persist the analysis with a single ``ON CONFLICT DO NOTHING`` upsert on
  the partial unique index ``(job_id, prompt_version) WHERE status =
  'succeeded'``, so a concurrent duplicate never creates a second row and
  never overwrites history.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.agents.job_analysis.contract import (
    JOB_ANALYSIS_AGENT_NAME,
    InsufficientJobContentError,
    JobAnalysisRequest,
    JobAnalysisResult,
    JobNotFoundError,
)
from app.agents.job_analysis.prompt import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    build_analysis_prompt,
)
from app.agents.runtime.context import RunContext
from app.agents.runtime.registry import HandlerRegistry
from app.clients.gemini import GeminiClient
from app.core.config import get_settings
from app.models.enums import RunStatus
from app.models.job import Job
from app.models.job_analysis import JobAnalysis


class JobAnalysisAgent:
    """Deterministic orchestrator around one injectable Gemini client."""

    def __init__(
        self,
        gemini: GeminiClient,
        *,
        prompt_version: str = PROMPT_VERSION,
    ) -> None:
        if not prompt_version or not prompt_version.strip():
            raise ValueError("prompt_version must not be empty")
        self._gemini = gemini
        self._prompt_version = prompt_version

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    @property
    def model(self) -> str | None:
        return getattr(self._gemini, "model", None)

    def handle(
        self, payload: dict | None, context: RunContext
    ) -> dict:
        """Runtime handler contract: validate the payload, then analyze."""
        request = JobAnalysisRequest.from_payload(payload)
        return self.analyze(context.session, request.job_id)

    def analyze(self, session: Session, job_id: UUID) -> dict:
        """Analyze one persisted job and return the stored envelope.

        A successful analysis for this job and prompt version short-circuits
        before any Gemini call, making repeated analysis deterministic and
        keeping the partial unique index intact. Missing jobs and jobs
        without usable content raise deterministic application errors; the
        runtime turns any raised error into its normal retry/failure path.
        """
        existing = self._find_succeeded(session, job_id)
        if existing is not None:
            return _envelope(
                job_id=existing.job_id,
                prompt_version=existing.prompt_version,
                model=existing.model,
                analysis=existing.analysis,
            )

        job = session.get(Job, job_id)
        if job is None:
            raise JobNotFoundError(f"job {job_id} does not exist")
        description = (job.description or "").strip()
        if not (job.title or "").strip() or not description:
            raise InsufficientJobContentError(
                f"job {job_id} has no usable title/description to analyze"
            )
        company = job.company
        prompt = build_analysis_prompt(
            title=job.title.strip(),
            description=description,
            company=company.name if company is not None else None,
            location=job.location,
            employment_type=job.employment_type,
            experience_level=job.experience_level,
        )

        result = self._gemini.generate_structured(
            prompt=prompt,
            schema=JobAnalysisResult,
            system_instruction=SYSTEM_INSTRUCTION,
        )

        return persist_analysis(
            session,
            job_id=job_id,
            result=result,
            model=self.model,
            prompt_version=self._prompt_version,
        )

    def _find_succeeded(self, session: Session, job_id: UUID) -> JobAnalysis | None:
        stmt = (
            select(JobAnalysis)
            .where(
                JobAnalysis.job_id == job_id,
                JobAnalysis.prompt_version == self._prompt_version,
                JobAnalysis.status == RunStatus.succeeded,
            )
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()


def persist_analysis(
    session: Session,
    *,
    job_id: UUID,
    result: JobAnalysisResult,
    model: str | None,
    prompt_version: str,
) -> dict:
    """Insert the analysis as ``succeeded``, or return the existing winner.

    ``ON CONFLICT ... DO NOTHING`` against the partial unique index makes
    the first writer win; a concurrent or bypassing duplicate reads the
    stored analysis instead of raising or overwriting it.
    """
    stmt = (
        pg_insert(JobAnalysis)
        .values(
            job_id=job_id,
            analysis=result.model_dump(mode="json"),
            model=model,
            prompt_version=prompt_version,
            status=RunStatus.succeeded,
        )
        .on_conflict_do_nothing(
            index_elements=[JobAnalysis.job_id, JobAnalysis.prompt_version],
            index_where=JobAnalysis.status == RunStatus.succeeded,
        )
        .returning(
            JobAnalysis.job_id,
            JobAnalysis.prompt_version,
            JobAnalysis.model,
            JobAnalysis.analysis,
        )
    )
    row = session.execute(stmt).first()
    if row is None:
        row = session.execute(
            select(
                JobAnalysis.job_id,
                JobAnalysis.prompt_version,
                JobAnalysis.model,
                JobAnalysis.analysis,
            ).where(
                JobAnalysis.job_id == job_id,
                JobAnalysis.prompt_version == prompt_version,
                JobAnalysis.status == RunStatus.succeeded,
            )
        ).first()
        if row is None:
            raise RuntimeError(
                f"analysis for job {job_id} / {prompt_version} vanished after conflict"
            )
    return _envelope(
        job_id=row.job_id,
        prompt_version=row.prompt_version,
        model=row.model,
        analysis=row.analysis,
    )


def _envelope(
    *,
    job_id: UUID,
    prompt_version: str,
    model: str | None,
    analysis: Mapping[str, object] | None,
) -> dict:
    return {
        "job_id": str(job_id),
        "prompt_version": prompt_version,
        "model": model,
        "analysis": dict(analysis) if analysis is not None else None,
    }


def register_job_analysis_handler(
    registry: HandlerRegistry,
    *,
    gemini: GeminiClient | None = None,
    prompt_version: str = PROMPT_VERSION,
    agent_name: str = JOB_ANALYSIS_AGENT_NAME,
) -> JobAnalysisAgent:
    """Build a JobAnalysisAgent and register its handler on ``registry``.

    A client not supplied is constructed from application settings — the
    production path used by ``create_app()``. The SDK client inside is
    built lazily, so registration never requires credentials. Tests
    inject a deterministic fake instead; only the given registry is
    mutated.
    """
    if gemini is None:
        settings = get_settings()
        gemini = GeminiClient(
            settings.gemini_model, api_key=settings.gemini_api_key
        )
    agent = JobAnalysisAgent(gemini, prompt_version=prompt_version)
    registry.register(agent_name, agent.handle)
    return agent


__all__ = [
    "JobAnalysisAgent",
    "persist_analysis",
    "register_job_analysis_handler",
]
