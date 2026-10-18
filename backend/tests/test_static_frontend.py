"""FastAPI static frontend and SPA routing tests using temporary files."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def static_client(tmp_path) -> TestClient:
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><body><div id='root'>frontend-shell</div></body></html>",
        encoding="utf-8",
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("window.appReady = true;", encoding="utf-8")
    return TestClient(create_app(frontend_dist_dir=tmp_path))


def test_root_and_client_routes_serve_spa_entrypoint(tmp_path) -> None:
    with static_client(tmp_path) as client:
        root = client.get("/")
        nested = client.get("/jobs/saved")

    assert root.status_code == 200
    assert nested.status_code == 200
    assert "frontend-shell" in root.text
    assert "frontend-shell" in nested.text


def test_static_assets_are_served(tmp_path) -> None:
    with static_client(tmp_path) as client:
        response = client.get("/assets/app.js")
    assert response.status_code == 200
    assert "window.appReady" in response.text


def test_unknown_api_paths_remain_404_instead_of_spa_fallback(tmp_path) -> None:
    with static_client(tmp_path) as client:
        get_response = client.get("/api/no-such-route")
        post_response = client.post("/api/no-such-route")

    assert get_response.status_code == 404
    assert get_response.json() == {"detail": "Not Found"}
    assert post_response.status_code == 404
    assert "frontend-shell" not in get_response.text


def test_health_and_openapi_routes_precede_static_fallback(tmp_path) -> None:
    with static_client(tmp_path) as client:
        health = client.get("/health")
        openapi = client.get("/openapi.json")

    assert health.status_code == 200
    assert health.json()["status"] == "healthy"
    assert openapi.status_code == 200
    assert openapi.headers["content-type"].startswith("application/json")


def test_static_mount_does_not_expose_files_outside_build_directory(tmp_path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("private-marker", encoding="utf-8")
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text("safe-shell", encoding="utf-8")

    with TestClient(create_app(frontend_dist_dir=build)) as client:
        response = client.get("/%2e%2e/outside.txt")

    assert "private-marker" not in response.text
