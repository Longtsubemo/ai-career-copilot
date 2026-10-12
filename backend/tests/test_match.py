"""Match reproducibility: a score is always tied to its exact inputs."""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import DocumentKind, RunStatus
from app.models.job import Job
from app.models.job_analysis import JobAnalysis
from app.models.match import Match
from tests.factories import make_candidate, make_job, make_master_resume


def build_inputs(session: Session):
    candidate = make_candidate(session)
    resume = make_master_resume(session, candidate)
    job = make_job(session)
    analysis = JobAnalysis(
        job_id=job.id,
        prompt_version="v1",
        status=RunStatus.succeeded,
        analysis={"required_skills": ["python"]},
    )
    session.add(analysis)
    session.flush()
    return candidate, resume, job, analysis


def add_match(session: Session, inputs, score: str = "82.50", prompt_version: str = "v1"):
    candidate, resume, job, analysis = inputs
    match = Match(
        job_id=job.id,
        candidate_id=candidate.id,
        resume_document_id=resume.id,
        job_analysis_id=analysis.id,
        score=Decimal(score),
        prompt_version=prompt_version,
    )
    session.add(match)
    session.flush()
    return match


def test_match_links_to_job_candidate_resume_and_analysis(session: Session) -> None:
    inputs = build_inputs(session)
    match = add_match(session, inputs)
    session.commit()
    session.expire_all()

    reloaded = session.get(Match, match.id)
    assert reloaded is not None
    assert reloaded.job.id == inputs[2].id
    assert reloaded.candidate.id == inputs[0].id
    assert reloaded.resume_document.id == inputs[1].id
    assert reloaded.resume_document.kind == DocumentKind.master_resume
    assert reloaded.job_analysis.id == inputs[3].id


def test_score_is_stored_with_fixed_precision(session: Session) -> None:
    inputs = build_inputs(session)
    match = add_match(session, inputs, score="82.50")
    session.commit()
    session.expire_all()

    reloaded = session.get(Match, match.id)
    assert reloaded is not None
    assert reloaded.score == Decimal("82.50")


@pytest.mark.parametrize("score", ["-0.01", "100.01", "150.00"])
def test_out_of_range_scores_are_rejected(session: Session, score: str) -> None:
    inputs = build_inputs(session)
    with pytest.raises(IntegrityError):
        add_match(session, inputs, score=score)
        session.commit()
    session.rollback()


@pytest.mark.parametrize("score", ["0.00", "50.25", "100.00"])
def test_boundary_scores_are_accepted(session: Session, score: str) -> None:
    inputs = build_inputs(session)
    add_match(session, inputs, score=score)
    session.commit()


def test_verdict_round_trips_through_jsonb(session: Session) -> None:
    inputs = build_inputs(session)
    verdict = {"matching_skills": ["python"], "missing_skills": ["k8s"], "rationale": "good"}
    match = add_match(session, inputs)
    match.verdict = verdict
    match.model = "gemini-2.5-flash"
    session.commit()
    session.expire_all()

    reloaded = session.get(Match, match.id)
    assert reloaded is not None
    assert reloaded.verdict == verdict
    assert reloaded.model == "gemini-2.5-flash"


def test_same_inputs_and_prompt_version_conflict(session: Session) -> None:
    inputs = build_inputs(session)
    add_match(session, inputs, prompt_version="v1")
    session.commit()

    with pytest.raises(IntegrityError):
        add_match(session, inputs, prompt_version="v1")
        session.commit()
    session.rollback()


def test_a_new_prompt_version_produces_a_new_match(session: Session) -> None:
    inputs = build_inputs(session)
    first = add_match(session, inputs, prompt_version="v1")
    second = add_match(session, inputs, prompt_version="v2")
    session.commit()

    assert first.id != second.id


def test_match_requires_a_job_analysis(session: Session) -> None:
    candidate = make_candidate(session)
    resume = make_master_resume(session, candidate)
    job = make_job(session)

    match = Match(
        job_id=job.id,
        candidate_id=candidate.id,
        resume_document_id=resume.id,
        job_analysis_id=None,
        score=Decimal("50.00"),
        prompt_version="v1",
    )
    session.add(match)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_match_is_removed_with_its_job(session: Session) -> None:
    inputs = build_inputs(session)
    add_match(session, inputs)
    session.commit()
    job_id = inputs[2].id

    session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
    session.commit()

    assert session.get(Job, job_id) is None
    assert session.query(Match).filter(Match.job_id == job_id).count() == 0


def test_candidate_relationship_exposes_matches(session: Session) -> None:
    inputs = build_inputs(session)
    add_match(session, inputs)
    session.commit()
    session.expire_all()

    reloaded_candidate = session.get(type(inputs[0]), inputs[0].id)
    assert reloaded_candidate is not None
    assert len(reloaded_candidate.matches) == 1