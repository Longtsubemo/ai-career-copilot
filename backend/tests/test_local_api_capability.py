from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.capability import is_valid_api_capability
from app.core.config import Settings
from app.desktop_runtime.__main__ import _read_api_capability
from app.desktop_runtime import supervisor

CAPABILITY_ONE = "a" * 64
CAPABILITY_TWO = "b" * 64
TAURI_DEV_ORIGIN = "http://localhost:5173"
TAURI_LOCAL_ORIGIN = "http://tauri.localhost"


def _desktop_settings(origins: list[str] | None = None) -> Settings:
    # Desktop mode requires a frontend build; CI runs backend tests before building it.
    frontend_dist = Path(tempfile.mkdtemp())
    (frontend_dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return Settings(
        _env_file=None,
        runtime_mode="desktop",
        environment="desktop",
        cors_origins=origins or [],
        frontend_dist_dir=str(frontend_dist),
    )


def _client(monkeypatch: pytest.MonkeyPatch, capability: str | None, origins=None):
    from app import main
    from app.api import origin

    settings = _desktop_settings(origins)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(origin, "get_settings", lambda: settings)
    return TestClient(main.create_app(api_capability=capability), base_url="http://localhost")


def test_desktop_api_and_health_routes_reject_missing_or_invalid_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _client(monkeypatch, CAPABILITY_ONE) as client:
        for path in (
            "/api",
            "/api/not-a-route",
            "/health",
            "/health/ready",
            "/docs",
            "/openapi.json",
        ):
            missing = client.get(path)
            invalid = client.get(
                path,
                headers={"Authorization": f"Bearer {'c' * 64}"},
            )
            assert missing.status_code == 401, path
            assert invalid.status_code == 401, path
            assert missing.headers["cache-control"] == "no-store"


def test_unauthorized_response_and_logs_never_echo_the_supplied_secret(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    supplied = "d" * 64
    with _client(monkeypatch, CAPABILITY_ONE) as client:
        response = client.get(
            "/api/candidates/me",
            headers={"Authorization": f"Bearer {supplied}"},
        )
    assert response.status_code == 401
    assert supplied not in response.text
    assert supplied not in caplog.text


def test_valid_capability_authenticates_health_and_api_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _client(monkeypatch, CAPABILITY_ONE) as client:
        headers = {"Authorization": f"Bearer {CAPABILITY_ONE}"}
        assert client.get("/health", headers=headers).status_code == 200
        assert client.get("/api/not-a-route", headers=headers).status_code == 404


def test_new_runtime_capability_invalidates_the_previous_runtime_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _client(monkeypatch, CAPABILITY_TWO) as restarted_client:
        assert restarted_client.get(
            "/health", headers={"Authorization": f"Bearer {CAPABILITY_ONE}"}
        ).status_code == 401
        assert restarted_client.get(
            "/health", headers={"Authorization": f"Bearer {CAPABILITY_TWO}"}
        ).status_code == 200


@pytest.mark.parametrize("origin", [TAURI_DEV_ORIGIN, TAURI_LOCAL_ORIGIN])
def test_authorization_header_preflight_is_limited_to_trusted_tauri_origins(
    monkeypatch: pytest.MonkeyPatch,
    origin: str,
) -> None:
    with _client(monkeypatch, CAPABILITY_ONE, [origin]) as client:
        response = client.options(
            "/api/candidates/me",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "authorization" in response.headers["access-control-allow-headers"].casefold()
    assert "*" not in response.headers["access-control-allow-headers"]


def test_unexpected_browser_origin_is_rejected_before_api_capability_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _client(monkeypatch, CAPABILITY_ONE, [TAURI_DEV_ORIGIN]) as client:
        response = client.get(
            "/api/jobs",
            headers={"Origin": "https://attacker.example"},
        )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_trusted_tauri_origin_can_pass_cross_site_fetch_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _client(monkeypatch, CAPABILITY_ONE, [TAURI_LOCAL_ORIGIN]) as client:
        response = client.get(
            "/api/not-a-route",
            headers={
                "Authorization": f"Bearer {CAPABILITY_ONE}",
                "Origin": TAURI_LOCAL_ORIGIN,
                "Sec-Fetch-Site": "cross-site",
            },
        )
    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == TAURI_LOCAL_ORIGIN


@pytest.mark.parametrize(
    "line",
    [
        "",
        "not-json\n",
        json.dumps({"command": "initialize", "api_capability": "short"}) + "\n",
        json.dumps({"command": "shutdown", "api_capability": CAPABILITY_ONE}) + "\n",
    ],
)
def test_native_startup_handshake_rejects_missing_or_invalid_capability(line: str) -> None:
    with pytest.raises(RuntimeError, match="handshake was invalid"):
        _read_api_capability(io.StringIO(line))


def test_native_startup_handshake_reads_the_private_pipe_value_without_echoing_it() -> None:
    line = json.dumps(
        {"command": "initialize", "api_capability": CAPABILITY_ONE}
    ) + "\n"
    assert _read_api_capability(io.StringIO(line)) == CAPABILITY_ONE
    assert is_valid_api_capability(CAPABILITY_ONE)
    assert not is_valid_api_capability(CAPABILITY_ONE.upper())


def test_backend_readiness_probe_supplies_the_capability_without_logging_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url: str, *, headers: dict[str, str]):
            observed["url"] = url
            observed["headers"] = headers
            return SimpleNamespace(status_code=200, json=lambda: {"status": "ready"})

    monkeypatch.setattr(supervisor.httpx, "Client", FakeClient)
    assert supervisor._http_readiness_check(
        "http://127.0.0.1:43210/health/ready", CAPABILITY_ONE
    )
    assert observed["headers"] == {"Authorization": f"Bearer {CAPABILITY_ONE}"}
    assert CAPABILITY_ONE not in json.dumps(observed.get("url"))


def test_desktop_app_without_a_native_capability_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _client(monkeypatch, None) as client:
        assert client.get("/health").status_code == 401
        assert client.get("/api/candidates/me").status_code == 401


def test_capability_is_not_written_to_desktop_configuration(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.desktop_runtime.paths import ApplicationPaths

    paths = ApplicationPaths(
        tmp_path / "install",
        tmp_path / "isolated-data" / "AI Career Copilot",
    )
    monkeypatch.setattr(ApplicationPaths, "_secure_directory", staticmethod(lambda _: None))
    paths.prepare()
    runtime = supervisor.DesktopRuntime(
        paths,
        tmp_path / "postgres-bin",
        api_capability=CAPABILITY_ONE,
    )
    payload = {
        "database_url": "postgresql+psycopg://local:test@127.0.0.1:50000/career_copilot",
        "runtime_mode": "desktop",
        "environment": "desktop",
        "cors_origins": [TAURI_LOCAL_ORIGIN],
        "enable_resume_ai": False,
        "debug": False,
    }
    runtime._save_desktop_settings(payload)
    persisted = paths.desktop_settings_path.read_text(encoding="utf-8")
    assert CAPABILITY_ONE not in persisted
    assert "api_capability" not in persisted
