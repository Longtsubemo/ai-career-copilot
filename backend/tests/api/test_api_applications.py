"""Behaviour of the application tracking endpoints."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select

from app.models.application import Application
from tests.api.support import register
from tests.factories import make_company, make_job


def _seed_job(session):
    company = make_company(session, name="Acme Inc.")
    return make_job(session, company, title="Senior AI Engineer")


def _candidate_id(client) -> str:
    return client.get("/api/candidates/me").json()["id"]


def test_applications_require_authentication(client):
    assert client.get("/api/applications").status_code == 401
    assert client.post("/api/applications", json={}).status_code == 401
    assert client.get(f"/api/applications/{uuid4()}").status_code == 401
    assert (
        client.patch(f"/api/applications/{uuid4()}", json={}).status_code == 401
    )


def test_create_application_without_a_job(client, session):
    register(client)
    candidate_id = _candidate_id(client)

    response = client.post("/api/applications", json={"notes": "first draft"})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["job_id"] is None
    assert body["job_title_snapshot"] is None
    assert body["company_name_snapshot"] is None
    assert body["notes"] == "first draft"

    stored = session.get(Application, body["id"])
    assert stored is not None
    assert str(stored.candidate_id) == candidate_id


def test_create_application_snapshots_the_job(client, session):
    register(client)
    job = _seed_job(session)

    response = client.post(
        "/api/applications",
        json={
            "job_id": str(job.id),
            "status": "applied",
            "notes": "referral",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["job_id"] == str(job.id)
    assert body["status"] == "applied"
    assert body["job_title_snapshot"] == "Senior AI Engineer"
    assert body["company_name_snapshot"] == "Acme Inc."


def test_create_application_with_unknown_job_is_404(client):
    register(client)

    response = client.post("/api/applications", json={"job_id": str(uuid4())})

    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"]


def test_create_application_cannot_choose_an_owner(client, session):
    register(client)

    response = client.post(
        "/api/applications",
        json={"notes": "ignored", "candidate_id": str(uuid4())},
    )

    assert response.status_code == 422
    assert (
        session.scalar(select(Application).where(Application.notes == "ignored"))
        is None
    )


def test_invalid_status_is_rejected(client):
    register(client)

    response = client.post(
        "/api/applications", json={"status": "not-a-status"}
    )

    assert response.status_code == 422


def test_list_and_filter_applications(client, session):
    register(client)
    job = _seed_job(session)
    client.post("/api/applications", json={"job_id": str(job.id)})
    client.post("/api/applications", json={"status": "rejected"})
    client.post("/api/applications", json={"status": "interview"})

    everything = client.get("/api/applications")
    assert everything.status_code == 200
    assert everything.json()["total"] == 3
    assert len(everything.json()["items"]) == 3

    rejected = client.get("/api/applications", params={"status": "rejected"})
    assert rejected.json()["total"] == 1
    assert rejected.json()["items"][0]["status"] == "rejected"

    page = client.get("/api/applications", params={"limit": 2, "offset": 0})
    assert len(page.json()["items"]) == 2
    assert page.json()["total"] == 3

    assert client.get("/api/applications", params={"limit": 0}).status_code == 422
    assert (
        client.get("/api/applications", params={"status": "nope"}).status_code
        == 422
    )


def test_read_one_application(client, session):
    register(client)
    job = _seed_job(session)
    created = client.post(
        "/api/applications", json={"job_id": str(job.id)}
    ).json()

    response = client.get(f"/api/applications/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_read_unknown_application_is_404(client):
    register(client)

    response = client.get(f"/api/applications/{uuid4()}")

    assert response.status_code == 404


def test_partial_update_changes_only_the_sent_fields(client, session):
    register(client)
    created = client.post(
        "/api/applications", json={"notes": "draft notes"}
    ).json()

    response = client.patch(
        f"/api/applications/{created['id']}",
        json={"status": "applied", "notes": "sent today"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "applied"
    assert body["notes"] == "sent today"
    assert body["job_id"] == created["job_id"]

    stored = session.get(Application, body["id"])
    assert stored.status.value == "applied"
    assert stored.updated_at >= stored.created_at


def test_update_rejects_an_empty_body(client):
    register(client)
    created = client.post("/api/applications", json={}).json()

    response = client.patch(f"/api/applications/{created['id']}", json={})

    assert response.status_code == 422


def test_update_rejects_a_null_status(client):
    register(client)
    created = client.post("/api/applications", json={}).json()

    response = client.patch(
        f"/api/applications/{created['id']}", json={"status": None}
    )

    assert response.status_code == 422


def test_update_rejects_unknown_fields(client):
    register(client)
    created = client.post("/api/applications", json={}).json()

    response = client.patch(
        f"/api/applications/{created['id']}", json={"job_id": str(uuid4())}
    )

    assert response.status_code == 422


def test_update_unknown_application_is_404(client):
    register(client)

    response = client.patch(
        f"/api/applications/{uuid4()}", json={"status": "applied"}
    )

    assert response.status_code == 404


def test_creating_a_draft_leaves_applied_at_null(client):
    register(client)

    created = client.post("/api/applications", json={}).json()

    assert created["status"] == "draft"
    assert created["applied_at"] is None


def test_creating_with_applied_status_records_a_server_timestamp(client):
    register(client)

    created = client.post("/api/applications", json={"status": "applied"}).json()

    assert created["status"] == "applied"
    assert created["applied_at"] is not None


def test_client_cannot_supply_applied_at_on_create(client):
    register(client)

    response = client.post(
        "/api/applications",
        json={"status": "applied", "applied_at": "2000-01-01T00:00:00Z"},
    )

    assert response.status_code == 422


def test_client_cannot_supply_applied_at_on_update(client):
    register(client)
    created = client.post("/api/applications", json={}).json()

    response = client.patch(
        f"/api/applications/{created['id']}",
        json={"applied_at": "2000-01-01T00:00:00Z"},
    )

    assert response.status_code == 422


def test_transition_to_applied_stamps_applied_at(client):
    register(client)
    created = client.post("/api/applications", json={}).json()
    assert created["applied_at"] is None

    updated = client.patch(
        f"/api/applications/{created['id']}", json={"status": "applied"}
    ).json()

    assert updated["status"] == "applied"
    assert updated["applied_at"] is not None


def test_notes_edit_after_applying_preserves_applied_at(client):
    register(client)
    created = client.post("/api/applications", json={"status": "applied"}).json()

    updated = client.patch(
        f"/api/applications/{created['id']}",
        json={"notes": "recruiter replied"},
    ).json()

    assert updated["notes"] == "recruiter replied"
    assert updated["applied_at"] == created["applied_at"]


def test_repeating_the_applied_status_preserves_applied_at(client):
    register(client)
    created = client.post("/api/applications", json={"status": "applied"}).json()

    updated = client.patch(
        f"/api/applications/{created['id']}", json={"status": "applied"}
    ).json()

    assert updated["applied_at"] == created["applied_at"]


def test_leaving_applied_preserves_the_submission_time(client):
    register(client)
    created = client.post("/api/applications", json={"status": "applied"}).json()

    updated = client.patch(
        f"/api/applications/{created['id']}", json={"status": "interview"}
    ).json()

    assert updated["status"] == "interview"
    assert updated["applied_at"] == created["applied_at"]


def test_advancing_without_applying_keeps_applied_at_null(client):
    register(client)
    created = client.post("/api/applications", json={}).json()

    updated = client.patch(
        f"/api/applications/{created['id']}", json={"status": "interview"}
    ).json()

    assert updated["applied_at"] is None


def test_partial_update_preserves_job_snapshot_and_applied_at(client, session):
    register(client)
    job = _seed_job(session)
    created = client.post(
        "/api/applications",
        json={"job_id": str(job.id), "status": "applied", "notes": "keep me"},
    ).json()

    updated = client.patch(
        f"/api/applications/{created['id']}", json={"notes": "changed"}
    ).json()

    assert updated["job_id"] == str(job.id)
    assert updated["job_title_snapshot"] == "Senior AI Engineer"
    assert updated["company_name_snapshot"] == "Acme Inc."
    assert updated["status"] == "applied"
    assert updated["notes"] == "changed"
    assert updated["applied_at"] == created["applied_at"]


def test_tracking_a_job_twice_creates_one_application(client, session):
    register(client)
    job = _seed_job(session)

    first = client.post("/api/applications", json={"job_id": str(job.id)})
    second = client.post("/api/applications", json={"job_id": str(job.id)})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert (
        session.scalar(
            select(func.count())
            .select_from(Application)
            .where(Application.job_id == job.id)
        )
        == 1
    )


def test_tracking_again_never_regresses_an_applied_record(client, session):
    register(client)
    job = _seed_job(session)
    created = client.post("/api/applications", json={"job_id": str(job.id)}).json()
    applied = client.patch(
        f"/api/applications/{created['id']}", json={"status": "applied"}
    ).json()

    again = client.post("/api/applications", json={"job_id": str(job.id)})

    assert again.status_code == 200
    assert again.json()["id"] == created["id"]
    assert again.json()["status"] == "applied"
    assert again.json()["applied_at"] == applied["applied_at"]
