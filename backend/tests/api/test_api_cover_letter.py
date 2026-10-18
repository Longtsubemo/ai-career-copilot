"""Behaviour of the cover letter endpoint."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.agents.cover_letter import CoverLetterResult
from app.agents.job_matching import MatchResult
from app.core.config import Settings
from app.models.agent_run import AgentRun
from app.models.document import Document
from app.models.enums import DocumentKind
from tests.api.support import LETTER, MATCH, build_full_world, build_job, login
from tests.fake_clients import FakeGeminiClient, SchemaScriptedGeminiClient
from tests.factories import make_document, make_user


def _documents(session) -> list[Document]:
    return list(session.execute(select(Document)).scalars().all())


def test_generate_a_cover_letter(client_factory, session):
    world = build_full_world(session)
    gemini = FakeGeminiClient(LETTER)
    client = client_factory(gemini=gemini)
    login(client, world.candidate.user)

    response = client.post(f"/api/jobs/{world.job.id}/cover-letter")

    assert response.status_code == 200
    body = response.json()
    assert body["agent_name"] == "cover_letter"
    assert body["status"] == "succeeded"
    output = body["output"]
    assert output["job_id"] == str(world.job.id)
    assert output["candidate_id"] == str(world.candidate.id)
    assert output["master_resume_document_id"] == str(world.resume.id)
    assert output["match_id"] == str(world.match.id)
    assert output["prompt_version"] == "cover-letter-v1"
    assert output["model"] == "gemini-3.8-flash"
    assert output["cover_letter"] == {
        "content": LETTER.content,
        "confidence": LETTER.confidence,
    }
    assert len(gemini.calls) == 1
    assert gemini.calls[0]["schema"] is CoverLetterResult

    letter = session.get(Document, UUID(output["document_id"]))
    assert letter is not None
    assert letter.kind is DocumentKind.cover_letter
    assert letter.candidate_id == world.candidate.id
    assert letter.job_id == world.job.id
    assert letter.source_document_id == world.resume.id
    assert letter.agent_run_id == UUID(body["id"])
    assert letter.model == "gemini-3.8-flash"
    assert letter.prompt_version == "cover-letter-v1"


def test_cover_letter_is_disabled_without_gemini_or_document_write(
    client_factory, session, monkeypatch
):
    world = build_full_world(session)
    gemini = FakeGeminiClient(LETTER)
    client = client_factory(gemini=gemini)
    login(client, world.candidate.user)
    before_documents = session.scalar(select(func.count()).select_from(Document))
    monkeypatch.setattr(
        "app.api.deps.get_settings",
        lambda: Settings(_env_file=None, enable_resume_ai=False),
    )

    response = client.post(f"/api/jobs/{world.job.id}/cover-letter")

    assert response.status_code == 403
    assert response.json() == {"detail": "resume AI features are disabled"}
    assert gemini.calls == []
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert session.scalar(select(func.count()).select_from(Document)) == before_documents


def test_repeated_generation_reuses_the_run_and_the_document(
    client_factory, session
):
    world = build_full_world(session)
    gemini = FakeGeminiClient(LETTER)
    client = client_factory(gemini=gemini)
    login(client, world.candidate.user)

    first = client.post(f"/api/jobs/{world.job.id}/cover-letter").json()
    second = client.post(f"/api/jobs/{world.job.id}/cover-letter").json()

    assert second["id"] == first["id"]
    assert second["output"] == first["output"]
    assert len(gemini.calls) == 1
    letters = [
        document
        for document in _documents(session)
        if document.kind is DocumentKind.cover_letter
    ]
    assert len(letters) == 1
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 1


def test_explicit_match_id_is_honoured(client_factory, session):
    world = build_full_world(session)
    gemini = FakeGeminiClient(LETTER)
    client = client_factory(gemini=gemini)
    login(client, world.candidate.user)

    response = client.post(
        f"/api/jobs/{world.job.id}/cover-letter",
        json={"match_id": str(world.match.id)},
    )

    assert response.status_code == 200
    assert response.json()["output"]["match_id"] == str(world.match.id)


def test_without_a_match_is_409_and_writes_nothing(client_factory, session):
    world = build_full_world(session)
    session.delete(world.match)
    session.flush()
    gemini = FakeGeminiClient(LETTER)
    client = client_factory(gemini=gemini)
    login(client, world.candidate.user)

    response = client.post(f"/api/jobs/{world.job.id}/cover-letter")

    assert response.status_code == 409
    assert "/match first" in response.json()["detail"]
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert all(
        document.kind is not DocumentKind.cover_letter
        for document in _documents(session)
    )
    assert gemini.calls == []


def test_unknown_match_id_is_404(client_factory, session):
    world = build_full_world(session)
    client = client_factory(gemini=FakeGeminiClient(LETTER))
    login(client, world.candidate.user)

    response = client.post(
        f"/api/jobs/{world.job.id}/cover-letter", json={"match_id": str(uuid4())}
    )

    assert response.status_code == 404
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_a_non_master_resume_is_rejected_by_the_agent(client_factory, session):
    world = build_full_world(session)
    tailored = make_document(
        session,
        world.candidate,
        DocumentKind.tailored_resume,
        "Tailored content.",
        source_document_id=world.resume.id,
    )
    gemini = SchemaScriptedGeminiClient(
        {MatchResult: MATCH, CoverLetterResult: LETTER}
    )
    client = client_factory(gemini=gemini)
    login(client, world.candidate.user)

    matched = client.post(
        f"/api/jobs/{world.job.id}/match",
        json={"resume_document_id": str(tailored.id)},
    )
    assert matched.status_code == 200

    response = client.post(
        f"/api/jobs/{world.job.id}/cover-letter",
        json={"resume_document_id": str(tailored.id)},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"].startswith("InvalidMasterResumeError")
    assert detail["run"]["status"] == "pending"
    assert len(gemini.calls) == 1
    assert all(
        document.kind is not DocumentKind.cover_letter
        for document in _documents(session)
    )


def test_without_a_candidate_is_404(client_factory, session):
    world = build_job(session)
    client = client_factory(gemini=FakeGeminiClient(LETTER))
    login(client, make_user(session))

    response = client.post(f"/api/jobs/{world.job.id}/cover-letter")

    assert response.status_code == 404
    assert "POST /api/candidates" in response.json()["detail"]


def test_unknown_job_is_404_without_a_run(client_factory, session):
    world = build_full_world(session)
    client = client_factory(gemini=FakeGeminiClient(LETTER))
    login(client, world.candidate.user)

    response = client.post(f"/api/jobs/{uuid4()}/cover-letter")

    assert response.status_code == 404
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
