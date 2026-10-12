"""Behaviour of the candidate profile endpoints."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from app.api.deps import NO_CANDIDATE_DETAIL
from app.models.candidate import Candidate
from tests.api.support import (
    CSRF_HEADER,
    login,
    make_user_client,
    register,
)
from tests.factories import make_user

PROFILE = "/api/candidates/me"


def test_register_then_read_current_candidate(client, session):
    body = register(client, name="Ada Lovelace")

    me = client.get("/api/candidates/me")
    assert me.status_code == 200
    created = me.json()
    assert created["name"] == "Ada Lovelace"

    listed = client.get("/api/candidates")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [created["id"]]

    stored = session.get(Candidate, created["id"])
    assert stored is not None
    assert str(stored.user_id) == body["id"]


def test_create_then_read_current_candidate(client, session):
    user = make_user(session)
    login(client, user)

    response = client.post(
        "/api/candidates",
        json={
            "name": "Ada Lovelace",
            "headline": "AI Engineer",
            "location": "Remote",
            "years_of_experience": 5,
            "seniority": "mid",
            "skills": ["python", "fastapi"],
            "summary": "Backend specialist.",
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "Ada Lovelace"
    assert created["years_of_experience"] == 5
    assert created["skills"] == ["python", "fastapi"]

    me = client.get("/api/candidates/me")
    assert me.status_code == 200
    assert me.json() == created

    stored = session.get(Candidate, created["id"])
    assert stored is not None
    assert stored.user_id == user.id


def test_second_candidate_is_rejected(client, session):
    register(client, name="Ada Lovelace")

    second = client.post("/api/candidates", json={"name": "Grace Hopper"})

    assert second.status_code == 409
    names = session.execute(select(Candidate.name)).scalars().all()
    assert names == ["Ada Lovelace"]


def test_create_candidate_requires_authentication(client):
    response = client.post("/api/candidates", json={"name": "Ada Lovelace"})

    assert response.status_code == 401


def test_read_current_candidate_without_auth_is_401(client):
    response = client.get("/api/candidates/me")

    assert response.status_code == 401


def test_read_current_candidate_without_a_profile_is_404(client_factory, session):
    user = make_user(session)
    client, _ = make_user_client(client_factory, session, user=user)

    response = client.get("/api/candidates/me")

    assert response.status_code == 404
    assert response.json() == {"detail": NO_CANDIDATE_DETAIL}


def test_candidate_list_starts_empty(client_factory, session):
    client, _ = make_user_client(client_factory, session)

    response = client.get("/api/candidates")

    assert response.status_code == 200
    assert response.json() == []


def test_name_is_trimmed_and_whitespace_collapsed(client_factory, session):
    client, _ = make_user_client(client_factory, session)

    response = client.post("/api/candidates", json={"name": "  Ada   Lovelace  "})

    assert response.status_code == 201
    assert response.json()["name"] == "Ada Lovelace"


def test_blank_name_is_rejected(client_factory, session):
    client, _ = make_user_client(client_factory, session)

    response = client.post("/api/candidates", json={"name": "   "})

    assert response.status_code == 422
    assert client.get("/api/candidates/me").status_code == 404


def test_unknown_field_is_rejected(client_factory, session):
    client, _ = make_user_client(client_factory, session)

    response = client.post(
        "/api/candidates", json={"name": "Ada Lovelace", "age": 36}
    )

    assert response.status_code == 422


def test_negative_experience_is_rejected(client_factory, session):
    client, _ = make_user_client(client_factory, session)

    response = client.post(
        "/api/candidates", json={"name": "Ada Lovelace", "years_of_experience": -1}
    )

    assert response.status_code == 422


def test_update_profile_requires_authentication(client) -> None:
    response = client.patch(PROFILE, json={"headline": "AI Engineer"})

    assert response.status_code == 401


def test_update_profile_requires_csrf_header(client) -> None:
    register(client, name="Ada Lovelace")
    del client.headers[CSRF_HEADER]

    response = client.patch(PROFILE, json={"headline": "AI Engineer"})

    assert response.status_code == 403


def test_update_profile_edits_editable_fields(client) -> None:
    register(client, name="Ada Lovelace")

    response = client.patch(
        PROFILE,
        json={
            "headline": "AI Engineer",
            "location": "Remote",
            "years_of_experience": 6,
            "seniority": "senior",
            "skills": ["python", "fastapi"],
            "summary": "Backend specialist.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["headline"] == "AI Engineer"
    assert body["location"] == "Remote"
    assert body["years_of_experience"] == 6
    assert body["seniority"] == "senior"
    assert body["skills"] == ["python", "fastapi"]
    assert body["summary"] == "Backend specialist."
    assert body["name"] == "Ada Lovelace"
    assert client.get(PROFILE).json() == body


def test_update_profile_preserves_unrelated_fields(client) -> None:
    register(client, name="Ada Lovelace")

    first = client.patch(PROFILE, json={"location": "Remote"}).json()
    assert first["location"] == "Remote"
    assert first["summary"] is None
    assert first["skills"] is None

    second = client.patch(PROFILE, json={"summary": "Hello"}).json()
    assert second["location"] == "Remote"
    assert second["summary"] == "Hello"


def test_update_profile_trims_name(client) -> None:
    register(client, name="Ada Lovelace")

    body = client.patch(PROFILE, json={"name": "  Grace   Hopper  "}).json()

    assert body["name"] == "Grace Hopper"


def test_update_profile_rejects_ownership_fields(client) -> None:
    register(client, name="Ada Lovelace")
    foreign = str(uuid4())

    for field in ("id", "user_id", "candidate_id", "created_at"):
        response = client.patch(PROFILE, json={field: foreign})
        assert response.status_code == 422, field

    assert client.get(PROFILE).json()["name"] == "Ada Lovelace"


def test_update_profile_rejects_an_empty_body(client) -> None:
    register(client, name="Ada Lovelace")

    assert client.patch(PROFILE, json={}).status_code == 422


def test_update_profile_rejects_a_null_or_blank_name(client) -> None:
    register(client, name="Ada Lovelace")

    assert client.patch(PROFILE, json={"name": None}).status_code == 422
    assert client.patch(PROFILE, json={"name": "   "}).status_code == 422


def test_update_profile_rejects_out_of_range_experience(client) -> None:
    register(client, name="Ada Lovelace")

    assert client.patch(PROFILE, json={"years_of_experience": -1}).status_code == 422
    assert client.patch(PROFILE, json={"years_of_experience": 101}).status_code == 422


def test_update_profile_never_creates_a_second_candidate(client, session) -> None:
    register(client, name="Ada Lovelace")

    before = session.execute(select(Candidate)).scalars().all()
    assert len(before) == 1

    client.patch(PROFILE, json={"headline": "AI Engineer"})

    after = session.execute(select(Candidate)).scalars().all()
    assert len(after) == 1
    assert after[0].headline == "AI Engineer"


def test_update_profile_only_touches_the_callers_profile(client_factory, session) -> None:
    ada_client = client_factory()
    register(ada_client, email="ada@example.com", name="Ada Lovelace")
    grace_client = client_factory()
    register(grace_client, email="grace@example.com", name="Grace Hopper")

    grace_client.patch(PROFILE, json={"headline": "Grace's headline"})

    ada = ada_client.get(PROFILE).json()
    grace = grace_client.get(PROFILE).json()
    assert ada["headline"] is None
    assert ada["name"] == "Ada Lovelace"
    assert grace["headline"] == "Grace's headline"
