"""Cover Letter agent: turn persisted evidence into a cover letter document.

The agent executes through the generic :class:`AgentRuntime` as the
``cover_letter`` handler and never influences runtime state transitions:
every Gemini outcome is data or an exception, and only the runtime decides
retry versus failure.

Execution order keeps database work away from the Gemini API call:

* validate the request, then deterministically load the job, the
  candidate, the uploaded master resume, the persisted match and the
  succeeded JobAnalysis that match was built from, checking every
  ownership and compatibility rule first;
* build the versioned prompt and call Gemini — no database work;
* validate the structured result (done at the client boundary);
* append exactly one ``cover_letter`` Document that points at the master
  resume as its source evidence.

Nothing is written on any failure path — a failed model call never leaves
a letter behind. The letter is generated from the uploaded master resume
unchanged: this agent never writes to the resume, never produces a
tailored resume and never re-runs job analysis. Duplicate prevention is
the AgentRuntime idempotency key alone — documents are append-only and
carry no uniqueness constraint, so a regeneration at a new prompt version
is a new row pointing at the same source resume and history is never
overwritten.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

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
)
from app.agents.cover_letter.prompt import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    build_cover_letter_prompt,
)
from app.agents.job_matching.contract import MatchResult
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


class CoverLetterAgent:
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
        """Runtime handler contract: validate the payload, then generate."""
        request = CoverLetterRequest.from_payload(payload)
        return self.generate(
            context.session,
            job_id=request.job_id,
            candidate_id=request.candidate_id,
            master_resume_document_id=request.master_resume_document_id,
            match_id=request.match_id,
            agent_run_id=context.run.id,
        )

    def generate(
        self,
        session: Session,
        *,
        job_id: UUID,
        candidate_id: UUID,
        master_resume_document_id: UUID,
        match_id: UUID,
        agent_run_id: UUID | None = None,
    ) -> dict:
        """Generate one grounded cover letter and return the envelope.

        Every prerequisite is a deterministic check performed before any
        model call; missing records and failed ownership or compatibility
        checks raise application errors, and the runtime turns any raised
        error into its normal retry/failure path. The single write happens
        after the structured result validates, so a failed generation never
        persists a letter.
        """
        job = session.get(Job, job_id)
        if job is None:
            raise JobNotFoundError(f"job {job_id} does not exist")

        candidate = session.get(Candidate, candidate_id)
        if candidate is None:
            raise CandidateNotFoundError(f"candidate {candidate_id} does not exist")

        resume = session.get(Document, master_resume_document_id)
        if resume is None:
            raise MasterResumeNotFoundError(
                f"master resume document {master_resume_document_id} does not exist"
            )
        if resume.candidate_id != candidate_id:
            raise InvalidMasterResumeError(
                f"master resume document {master_resume_document_id} does not "
                f"belong to candidate {candidate_id}"
            )
        if resume.kind != DocumentKind.master_resume:
            raise InvalidMasterResumeError(
                f"document {master_resume_document_id} has kind "
                f"{resume.kind.value!r}; expected master_resume"
            )
        if not (resume.content or "").strip():
            raise InvalidMasterResumeError(
                f"master resume document {master_resume_document_id} has no content"
            )

        match = session.get(Match, match_id)
        if match is None:
            raise MatchNotFoundError(f"match {match_id} does not exist")
        if match.job_id != job_id:
            raise InvalidMatchError(
                f"match {match_id} does not belong to job {job_id}"
            )
        if match.candidate_id != candidate_id:
            raise InvalidMatchError(
                f"match {match_id} does not belong to candidate {candidate_id}"
            )
        if match.resume_document_id != master_resume_document_id:
            raise InvalidMatchError(
                f"match {match_id} was computed against resume document "
                f"{match.resume_document_id}, not {master_resume_document_id}"
            )
        match_result = _load_match_result(match)

        analysis = session.get(JobAnalysis, match.job_analysis_id)
        if analysis is None:
            raise JobAnalysisNotFoundError(
                f"match {match_id} references job analysis "
                f"{match.job_analysis_id}, which does not exist"
            )
        if analysis.job_id != job_id:
            raise InvalidMatchError(
                f"match {match_id} references job analysis {analysis.id}, which "
                f"belongs to job {analysis.job_id} rather than {job_id}"
            )
        if analysis.status != RunStatus.succeeded or analysis.analysis is None:
            raise JobAnalysisNotFoundError(
                f"match {match_id} references job analysis {analysis.id}, which "
                "is not a succeeded analysis with a stored payload"
            )

        company = job.company
        prompt = build_cover_letter_prompt(
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
            resume_content=resume.content,
            match=match_result,
        )

        result = self._gemini.generate_structured(
            prompt=prompt,
            schema=CoverLetterResult,
            system_instruction=SYSTEM_INSTRUCTION,
        )

        return persist_cover_letter(
            session,
            job_id=job_id,
            candidate_id=candidate_id,
            master_resume_document_id=master_resume_document_id,
            match_id=match_id,
            result=result,
            model=self.model,
            prompt_version=self._prompt_version,
            agent_run_id=agent_run_id,
        )


def persist_cover_letter(
    session: Session,
    *,
    job_id: UUID,
    candidate_id: UUID,
    master_resume_document_id: UUID,
    match_id: UUID,
    result: CoverLetterResult,
    model: str | None,
    prompt_version: str,
    agent_run_id: UUID | None = None,
) -> dict:
    """Append the cover letter document and return the stable envelope.

    The letter is a new immutable row: ``source_document_id`` points at
    the master resume it was generated from, never at another letter, and
    ``application_id`` stays null because application tracking is a later
    product concern. An earlier letter is never updated and never
    overwritten — regeneration appends.
    """
    document = Document(
        kind=DocumentKind.cover_letter,
        candidate_id=candidate_id,
        job_id=job_id,
        source_document_id=master_resume_document_id,
        application_id=None,
        agent_run_id=agent_run_id,
        content=result.content,
        model=model,
        prompt_version=prompt_version,
    )
    session.add(document)
    session.flush()
    session.refresh(document)

    return CoverLetterEnvelope(
        job_id=job_id,
        candidate_id=candidate_id,
        master_resume_document_id=master_resume_document_id,
        match_id=match_id,
        document_id=document.id,
        prompt_version=prompt_version,
        model=model,
        cover_letter=result,
    ).model_dump(mode="json")


def _load_match_result(match: Match) -> MatchResult:
    """Re-validate the stored verdict payload into the matching schema.

    The match row is evidence, so its payload must still describe a
    coherent assessment before it is handed to the model; a corrupt or
    truncated verdict is a precondition failure, not something to paper
    over with partial data.
    """
    detail = dict(match.verdict) if match.verdict is not None else {}
    detail["score"] = float(match.score)
    try:
        return MatchResult.model_validate(detail)
    except ValidationError as exc:
        raise InvalidMatchError(
            f"match {match.id} has an unusable verdict payload"
        ) from exc


def register_cover_letter_handler(
    registry: HandlerRegistry,
    *,
    gemini: GeminiClient | None = None,
    prompt_version: str = PROMPT_VERSION,
    agent_name: str = COVER_LETTER_AGENT_NAME,
) -> CoverLetterAgent:
    """Build a CoverLetterAgent and register its handler on ``registry``.

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
    agent = CoverLetterAgent(gemini, prompt_version=prompt_version)
    registry.register(agent_name, agent.handle)
    return agent


__all__ = [
    "CoverLetterAgent",
    "persist_cover_letter",
    "register_cover_letter_handler",
]
