"""Job Matching agent: evaluate a candidate's fit for a persisted job.

The agent executes through the generic :class:`AgentRuntime` as the
``job_matching`` handler and never influences runtime state transitions:
every Gemini outcome is data or an exception, and only the runtime decides
retry versus failure.

Execution order keeps database work away from the Gemini API call:

* validate the request, then load job, candidate and resume document and
  check every deterministic prerequisite (existence, ownership, kind,
  content, profile substance);
* short-circuit on an existing match for the same computation, so repeat
  requests never re-invoke Gemini;
* select the newest succeeded JobAnalysis — never re-analysing the job;
* build the versioned prompt and call Gemini — no database work;
* validate the structured result (done at the client boundary);
* persist the match with a single ``ON CONFLICT DO NOTHING`` upsert on
  the unique index ``(job_id, resume_document_id, prompt_version)``, so a
  concurrent duplicate never creates a second row and never overwrites
  history.

Nothing is written on any failure path — a failed model call never
leaves a successful match behind.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.agents.job_matching.contract import (
    JOB_MATCHING_AGENT_NAME,
    CandidateNotFoundError,
    InsufficientCandidateProfileError,
    InvalidResumeDocumentError,
    JobAnalysisNotFoundError,
    JobMatchingRequest,
    JobNotFoundError,
    MatchEnvelope,
    MatchResult,
    ResumeDocumentNotFoundError,
)
from app.agents.job_matching.prompt import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    build_matching_prompt,
)
from app.agents.runtime.context import RunContext
from app.agents.runtime.registry import HandlerRegistry
from app.clients.gemini import GeminiClient
from app.core.config import get_settings
from app.models.candidate import Candidate
from app.models.document import Document
from app.models.enums import DocumentKind, RunStatus
from app.models.job import Job
from app.models.job_analysis import JobAnalysis
from app.models.match import Match

RESUME_DOCUMENT_KINDS = (DocumentKind.master_resume, DocumentKind.tailored_resume)


class JobMatchingAgent:
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

    def handle(self, payload: dict | None, context: RunContext) -> dict:
        """Runtime handler contract: validate the payload, then match."""
        request = JobMatchingRequest.from_payload(payload)
        return self.match(
            context.session,
            job_id=request.job_id,
            candidate_id=request.candidate_id,
            resume_document_id=request.resume_document_id,
        )

    def match(
        self,
        session: Session,
        *,
        job_id: UUID,
        candidate_id: UUID,
        resume_document_id: UUID,
    ) -> dict:
        """Match one candidate/resume against one job and return the envelope.

        Deterministic prerequisites are checked before any model call;
        an existing match for the same computation short-circuits so
        repeated requests stay deterministic. Missing records and failed
        prerequisite checks raise deterministic application errors; the
        runtime turns any raised error into its normal retry/failure path.
        """
        job = session.get(Job, job_id)
        if job is None:
            raise JobNotFoundError(f"job {job_id} does not exist")

        candidate = session.get(Candidate, candidate_id)
        if candidate is None:
            raise CandidateNotFoundError(f"candidate {candidate_id} does not exist")

        document = session.get(Document, resume_document_id)
        if document is None:
            raise ResumeDocumentNotFoundError(
                f"resume document {resume_document_id} does not exist"
            )
        if document.candidate_id != candidate_id:
            raise InvalidResumeDocumentError(
                f"resume document {resume_document_id} does not belong to "
                f"candidate {candidate_id}"
            )
        if document.kind not in RESUME_DOCUMENT_KINDS:
            raise InvalidResumeDocumentError(
                f"resume document {resume_document_id} has kind "
                f"{document.kind.value!r}; expected a resume kind"
            )
        if not (document.content or "").strip():
            raise InvalidResumeDocumentError(
                f"resume document {resume_document_id} has no content to match against"
            )
        if _profile_is_empty(candidate):
            raise InsufficientCandidateProfileError(
                f"candidate {candidate_id} has no profile evidence to match against"
            )

        existing = _find_existing_match(
            session, job_id, resume_document_id, self._prompt_version
        )
        if existing is not None:
            return existing

        analysis = _select_succeeded_analysis(session, job_id)
        if analysis is None:
            raise JobAnalysisNotFoundError(
                f"no succeeded job analysis with a stored payload for job {job_id}"
            )

        company = job.company
        prompt = build_matching_prompt(
            job_title=job.title,
            company_name=company.name if company is not None else None,
            job_location=job.location,
            employment_type=job.employment_type,
            experience_level=job.experience_level,
            analysis=analysis.analysis or {},
            candidate_name=candidate.name,
            headline=candidate.headline,
            candidate_location=candidate.location,
            years_of_experience=candidate.years_of_experience,
            seniority=candidate.seniority,
            skills=candidate.skills,
            summary=candidate.summary,
            resume_kind=document.kind.value,
            resume_content=document.content,
        )

        result = self._gemini.generate_structured(
            prompt=prompt,
            schema=MatchResult,
            system_instruction=SYSTEM_INSTRUCTION,
        )

        return persist_match(
            session,
            job_id=job_id,
            candidate_id=candidate_id,
            resume_document_id=resume_document_id,
            job_analysis_id=analysis.id,
            result=result,
            model=self.model,
            prompt_version=self._prompt_version,
        )


def persist_match(
    session: Session,
    *,
    job_id: UUID,
    candidate_id: UUID,
    resume_document_id: UUID,
    job_analysis_id: UUID,
    result: MatchResult,
    model: str | None,
    prompt_version: str,
) -> dict:
    """Insert the match, or return the existing winner of the unique scope.

    ``ON CONFLICT DO NOTHING`` against the plain unique index
    ``(job_id, resume_document_id, prompt_version)`` makes the first
    writer win; a concurrent or bypassing duplicate reads the stored row
    instead of raising or overwriting history. The ``score`` column
    carries the bounded numeric for querying while the JSONB ``verdict``
    column carries the full structured detail (everything but the score),
    so the stored payload is self-contained.
    """
    detail = result.model_dump(mode="json")
    score = detail.pop("score")

    stmt = (
        pg_insert(Match)
        .values(
            job_id=job_id,
            candidate_id=candidate_id,
            resume_document_id=resume_document_id,
            job_analysis_id=job_analysis_id,
            score=score,
            verdict=detail,
            model=model,
            prompt_version=prompt_version,
        )
        .on_conflict_do_nothing(
            index_elements=[
                Match.job_id,
                Match.resume_document_id,
                Match.prompt_version,
            ]
        )
        .returning(
            Match.job_id,
            Match.candidate_id,
            Match.resume_document_id,
            Match.job_analysis_id,
            Match.score,
            Match.verdict,
            Match.model,
            Match.prompt_version,
        )
    )
    row = session.execute(stmt).first()
    if row is None:
        row = _find_existing_match_row(
            session, job_id, resume_document_id, prompt_version
        )
        if row is None:
            raise RuntimeError(
                f"match for job {job_id} / resume {resume_document_id} / "
                f"{prompt_version} vanished after conflict"
            )
    return _envelope(row)


def _find_existing_match(
    session: Session,
    job_id: UUID,
    resume_document_id: UUID,
    prompt_version: str,
) -> dict | None:
    row = _find_existing_match_row(session, job_id, resume_document_id, prompt_version)
    if row is None:
        return None
    return _envelope(row)


def _find_existing_match_row(
    session: Session,
    job_id: UUID,
    resume_document_id: UUID,
    prompt_version: str,
) -> Match | None:
    stmt = (
        select(Match)
        .where(
            Match.job_id == job_id,
            Match.resume_document_id == resume_document_id,
            Match.prompt_version == prompt_version,
        )
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _select_succeeded_analysis(session: Session, job_id: UUID) -> JobAnalysis | None:
    """Return the newest usable succeeded analysis for this job.

    Newest means ``created_at`` descending with ``id`` descending as the
    tie-break, so selection is deterministic; pending and failed rows and
    succeeded rows without a stored payload are ignored. Exactly one
    analysis version feeds one prompt — versions are never mixed.
    """
    stmt = (
        select(JobAnalysis)
        .where(
            JobAnalysis.job_id == job_id,
            JobAnalysis.status == RunStatus.succeeded,
            JobAnalysis.analysis.is_not(None),
        )
        .order_by(JobAnalysis.created_at.desc(), JobAnalysis.id.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _profile_is_empty(candidate: Candidate) -> bool:
    """True when the profile contributes no structured evidence at all.

    ``name`` is required for every candidate and ``location`` alone
    cannot ground a fit assessment, so neither counts toward substance.
    """
    return not any(
        (
            (candidate.summary or "").strip(),
            candidate.skills,
            (candidate.headline or "").strip(),
            (candidate.seniority or "").strip(),
            candidate.years_of_experience is not None,
        )
    )


def _envelope(record: Match | Row) -> dict:
    detail = dict(record.verdict) if record.verdict is not None else {}
    detail["score"] = float(record.score)
    try:
        match = MatchResult.model_validate(detail)
    except ValidationError as exc:
        raise RuntimeError(
            f"stored match for job {record.job_id} / {record.prompt_version} "
            "has an unusable verdict payload"
        ) from exc
    return MatchEnvelope(
        job_id=record.job_id,
        candidate_id=record.candidate_id,
        resume_document_id=record.resume_document_id,
        job_analysis_id=record.job_analysis_id,
        prompt_version=record.prompt_version,
        model=record.model,
        match=match,
    ).model_dump(mode="json")


def register_job_matching_handler(
    registry: HandlerRegistry,
    *,
    gemini: GeminiClient | None = None,
    prompt_version: str = PROMPT_VERSION,
    agent_name: str = JOB_MATCHING_AGENT_NAME,
) -> JobMatchingAgent:
    """Build a JobMatchingAgent and register its handler on ``registry``.

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
    agent = JobMatchingAgent(gemini, prompt_version=prompt_version)
    registry.register(agent_name, agent.handle)
    return agent


__all__ = [
    "RESUME_DOCUMENT_KINDS",
    "JobMatchingAgent",
    "persist_match",
    "register_job_matching_handler",
]
