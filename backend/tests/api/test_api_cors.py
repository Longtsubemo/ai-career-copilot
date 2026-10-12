"""CORS behaviour of the HTTP API.

The React dev server runs on another origin than FastAPI, so the app
factory installs a CORS middleware for an explicit, configurable origin
list. Nothing here reaches the network: ``TestClient`` sends the Origin
header and the middleware answers it in-process.
"""

from __future__ import annotations


def test_allowed_origin_is_echoed(client):
    response = client.get(
        "/health", headers={"Origin": "http://localhost:5173"}
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_unknown_origin_is_not_allowed(client):
    response = client.get(
        "/health", headers={"Origin": "https://evil.example.com"}
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_preflight_for_a_simple_get(client):
    response = client.options(
        "/api/jobs",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "GET" in response.headers["access-control-allow-methods"]
