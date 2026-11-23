"""The local profile initializes once and remains editable without accounts."""

from sqlalchemy import select

from app.models.candidate import Candidate
from app.services.candidates import LOCAL_CANDIDATE_ID
from tests.factories import make_candidate

PROFILE = "/api/candidates/me"


def test_first_open_creates_one_stable_empty_profile(client, session):
    first = client.get(PROFILE)
    second = client.get(PROFILE)

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"] == str(LOCAL_CANDIDATE_ID)
    assert first.json()["name"] == ""
    assert session.execute(select(Candidate)).scalars().all()[0].is_local_profile


def test_profile_can_be_completed_and_survives_request_reloads(client):
    assert client.get(PROFILE).status_code == 200
    response = client.patch(
        PROFILE,
        json={
            "name": "Ada Lovelace",
            "headline": "AI Engineer",
            "location": "Remote",
            "years_of_experience": 6,
            "seniority": "senior",
            "skills": ["python", "fastapi"],
            "summary": "Backend specialist.",
        },
    )

    assert response.status_code == 200
    assert client.get(PROFILE).json() == response.json()


def test_profile_update_preserves_omitted_values(client):
    client.get(PROFILE)
    client.patch(PROFILE, json={"name": "Ada Lovelace", "location": "Remote"})

    second = client.patch(PROFILE, json={"summary": "Hello"})

    assert second.status_code == 200
    assert second.json()["location"] == "Remote"
    assert second.json()["summary"] == "Hello"


def test_profile_rejects_identity_and_owner_fields(client):
    client.get(PROFILE)
    for field, value in (
        ("id", "00000000-0000-0000-0000-000000000001"),
        ("user_id", "00000000-0000-0000-0000-000000000002"),
        ("candidate_id", "00000000-0000-0000-0000-000000000003"),
        ("is_local_profile", False),
        ("created_at", "2026-01-01T00:00:00Z"),
    ):
        assert client.patch(PROFILE, json={field: value}).status_code == 422


def test_profile_rejects_empty_or_invalid_updates(client):
    client.get(PROFILE)
    assert client.patch(PROFILE, json={}).status_code == 422
    assert client.patch(PROFILE, json={"name": "   "}).status_code == 422
    assert client.patch(PROFILE, json={"years_of_experience": 101}).status_code == 422


def test_auth_endpoints_are_not_registered(client):
    for path in ("/api/auth/login", "/api/auth/register", "/api/auth/logout", "/api/auth/me"):
        assert client.post(path, json={}).status_code == 404


def test_ambiguous_profiles_are_preserved_and_never_selected_automatically(
    client_factory, session
):
    first = make_candidate(session, name="Ada")
    second = make_candidate(session, name="Grace")
    client = client_factory()

    response = client.get(PROFILE)

    assert response.status_code == 409
    assert "select_local_profile --list" in response.json()["detail"]
    assert session.get(Candidate, first.id).is_local_profile is False
    assert session.get(Candidate, second.id).is_local_profile is False
