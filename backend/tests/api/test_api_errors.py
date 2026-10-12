"""Cross-cutting error behaviour of the HTTP API."""

from __future__ import annotations

from app.api.errors import INTERNAL_ERROR_MESSAGE
from tests.api.support import login
from tests.factories import make_user


def test_unknown_path_is_a_json_404(client):
    response = client.get("/api/there-is-no-such-route")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}


def test_validation_failures_are_422_with_a_detail_list(client_factory, session):
    client = _authenticated(client_factory, session)

    response = client.post("/api/candidates", json={"name": 42})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert detail[0]["loc"] == ["body", "name"]


def test_unexpected_errors_are_opaque_500s(client_factory, session, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("secret-token-xyz")

    monkeypatch.setattr("app.services.candidates.candidate_for_user", explode)
    client = _authenticated(client_factory, session, raise_server_exceptions=False)

    response = client.get("/api/candidates/me")

    assert response.status_code == 500
    assert response.json() == {"detail": INTERNAL_ERROR_MESSAGE}
    assert "secret-token-xyz" not in response.text
    assert "Traceback" not in response.text
    assert "File \"" not in response.text


def _authenticated(client_factory, session, **kwargs):
    client = client_factory(**kwargs)
    login(client, make_user(session))
    return client


def test_health_still_answers_at_the_root(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_openapi_documents_every_endpoint_group(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in (
        "/api/candidates",
        "/api/candidates/me",
        "/api/resumes/master",
        "/api/resumes/master/history",
        "/api/jobs",
        "/api/jobs/{job_id}",
        "/api/jobs/discover",
        "/api/jobs/{job_id}/analyze",
        "/api/jobs/{job_id}/match",
        "/api/jobs/{job_id}/cover-letter",
        "/api/applications",
        "/api/applications/{application_id}",
    ):
        assert path in paths
    assert "post" in paths["/api/jobs/discover"]
    assert "patch" in paths["/api/applications/{application_id}"]
