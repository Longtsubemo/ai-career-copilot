"""The assisted application workflow across endpoints.

Applying is a human action: the system prepares materials and records the
outcome, but generating a cover letter or opening a posting must never claim a
submission. These tests pin that separation at the HTTP boundary.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.application import Application
from tests.api.support import LETTER, build_full_world, login
from tests.fake_clients import FakeGeminiClient


def _application_count(session) -> int:
    return session.scalar(select(func.count()).select_from(Application))


def test_generating_a_cover_letter_does_not_track_the_job(
    client_factory, session
):
    world = build_full_world(session)
    client = client_factory(gemini=FakeGeminiClient(LETTER))
    login(client, world.candidate.user)

    response = client.post(f"/api/jobs/{world.job.id}/cover-letter")

    assert response.status_code == 200
    assert _application_count(session) == 0


def test_reading_a_job_records_nothing(client_factory, session):
    world = build_full_world(session)
    client = client_factory(gemini=FakeGeminiClient(LETTER))
    login(client, world.candidate.user)

    assert client.get(f"/api/jobs/{world.job.id}").status_code == 200
    assert client.get("/api/jobs").status_code == 200

    assert _application_count(session) == 0


def test_tracking_after_preparing_materials_is_still_a_draft(
    client_factory, session
):
    world = build_full_world(session)
    client = client_factory(gemini=FakeGeminiClient(LETTER))
    login(client, world.candidate.user)

    assert client.post(f"/api/jobs/{world.job.id}/cover-letter").status_code == 200
    tracked = client.post(
        "/api/applications", json={"job_id": str(world.job.id)}
    )

    assert tracked.status_code == 201
    body = tracked.json()
    assert body["status"] == "draft"
    assert body["applied_at"] is None


def test_marking_applied_is_an_explicit_separate_step(client_factory, session):
    world = build_full_world(session)
    client = client_factory(gemini=FakeGeminiClient(LETTER))
    login(client, world.candidate.user)

    client.post(f"/api/jobs/{world.job.id}/cover-letter")
    tracked = client.post(
        "/api/applications", json={"job_id": str(world.job.id)}
    ).json()

    applied = client.patch(
        f"/api/applications/{tracked['id']}", json={"status": "applied"}
    ).json()

    assert applied["status"] == "applied"
    assert applied["applied_at"] is not None
    assert applied["job_id"] == str(world.job.id)
