"""Production settings validation without environment or database access."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.fixture(autouse=True)
def no_career_copilot_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in os.environ:
        if key.startswith("CAREER_COPILOT_"):
            monkeypatch.delenv(key, raising=False)


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "database_url": "postgresql+psycopg://prototype:placeholder@db.example/app?sslmode=require",
        "session_cookie_secure": True,
        "search_provider": "firecrawl",
        "firecrawl_base_url": "https://api.firecrawl.dev",
        "firecrawl_api_key": "test-only-key",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_local_defaults_keep_searxng_and_development_cors() -> None:
    settings = Settings(_env_file=None)
    assert settings.search_provider == "searxng"
    assert "http://localhost:5173" in settings.cors_origins
    assert settings.enable_resume_ai is True
    assert settings.discovery_daily_user_limit == 100
    assert settings.discovery_daily_global_limit == 1_000


def test_hosted_defaults_disable_resume_ai_and_use_stricter_quotas() -> None:
    settings = production_settings()
    assert settings.enable_resume_ai is False
    assert settings.discovery_daily_user_limit == 3
    assert settings.discovery_daily_global_limit == 20


def test_production_resume_ai_requires_explicit_enablement() -> None:
    settings = production_settings(enable_resume_ai=True)
    assert settings.enable_resume_ai is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"discovery_daily_user_limit": 0},
        {"discovery_daily_global_limit": 0},
        {"discovery_daily_user_limit": 10_001},
        {"discovery_daily_global_limit": 100_001},
    ],
)
def test_discovery_limits_are_validated(overrides: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        production_settings(**overrides)


def test_production_requires_an_explicit_database_url() -> None:
    with pytest.raises(ValidationError, match="must be explicitly configured"):
        Settings(
            _env_file=None,
            environment="production",
            session_cookie_secure=True,
            search_provider="firecrawl",
            firecrawl_base_url="https://api.firecrawl.dev",
            firecrawl_api_key="test-only-key",
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"debug": True}, "DEBUG must be false"),
        ({"session_cookie_secure": False}, "SESSION_COOKIE_SECURE must be true"),
        (
            {"database_url": "postgresql+psycopg://u:p@db.example/app"},
            "sslmode=require or stronger",
        ),
        (
            {"firecrawl_api_key": None},
            "FIRECRAWL_API_KEY is required",
        ),
        (
            {"firecrawl_base_url": "http://api.firecrawl.dev"},
            "must be an HTTPS service URL",
        ),
    ],
)
def test_production_rejects_insecure_or_incomplete_configuration(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        production_settings(**overrides)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.firecrawl.dev:bad",
        "https://api.firecrawl.dev:65536",
        "https://api.firecrawl.dev:",
    ],
)
def test_production_rejects_malformed_or_out_of_range_service_ports(
    base_url: str,
) -> None:
    with pytest.raises(ValidationError, match="must be an HTTPS service URL"):
        production_settings(firecrawl_base_url=base_url)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://prototype:placeholder@db.example:bad/app?sslmode=require",
        "postgresql+psycopg://prototype:placeholder@db.example:65536/app?sslmode=require",
        "postgresql+psycopg://prototype:placeholder@db.example:/app?sslmode=require",
    ],
)
def test_production_rejects_malformed_or_out_of_range_database_ports(
    database_url: str,
) -> None:
    with pytest.raises(
        ValidationError,
        match="PostgreSQL URL with sslmode=require or stronger",
    ):
        production_settings(database_url=database_url)


def test_production_accepts_valid_explicit_service_and_database_ports() -> None:
    settings = production_settings(
        firecrawl_base_url="https://api.firecrawl.dev:8443",
        database_url=(
            "postgresql://prototype:placeholder@db.example:5432/app?sslmode=require"
        ),
    )

    assert settings.firecrawl_base_url == "https://api.firecrawl.dev:8443"
    assert settings.database_url == (
        "postgresql+psycopg://prototype:placeholder@db.example:5432/app?sslmode=require"
    )


def test_production_uses_empty_cors_default_and_keeps_secure_settings() -> None:
    settings = production_settings()
    assert settings.cors_origins == []
    assert settings.session_cookie_secure is True
    assert settings.debug is False


def test_production_normalizes_neon_postgresql_url_for_psycopg3() -> None:
    settings = production_settings(
        database_url="postgresql://prototype:placeholder@db.example/app?sslmode=require"
    )
    assert settings.database_url.startswith("postgresql+psycopg://")


def test_production_rejects_explicit_development_cors_origin() -> None:
    with pytest.raises(ValidationError, match="must not include development origins"):
        production_settings(cors_origins=["http://localhost:5173"])


def test_production_rejects_unconfigured_searxng_provider() -> None:
    with pytest.raises(ValidationError, match="SEARXNG_BASE_URL must be explicitly"):
        production_settings(search_provider="searxng")


def test_production_accepts_explicit_https_searxng_provider() -> None:
    settings = production_settings(
        search_provider="searxng",
        searxng_base_url="https://search.example.test",
    )
    assert settings.search_provider == "searxng"


def test_selected_firecrawl_provider_requires_key_without_echoing_secrets() -> None:
    with pytest.raises(ValidationError) as caught:
        production_settings(
            search_provider="firecrawl",
            firecrawl_api_key=None,
            database_url=(
                "postgresql+psycopg://prototype:db-secret-marker@db.example/app"
                "?sslmode=require"
            ),
        )
    assert "FIRECRAWL_API_KEY is required" in str(caught.value)
    assert "db-secret-marker" not in str(caught.value)


def test_firecrawl_provider_accepts_configured_key() -> None:
    settings = production_settings()
    assert settings.search_provider == "firecrawl"


def test_render_environment_variables_satisfy_explicit_production_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "CAREER_COPILOT_ENVIRONMENT": "production",
        "CAREER_COPILOT_DEBUG": "false",
        "CAREER_COPILOT_DATABASE_URL": (
            "postgresql://prototype:placeholder@db.example/app?sslmode=require"
        ),
        "CAREER_COPILOT_SESSION_COOKIE_SECURE": "true",
        "CAREER_COPILOT_SEARCH_PROVIDER": "firecrawl",
        "CAREER_COPILOT_FIRECRAWL_BASE_URL": "https://api.firecrawl.dev",
        "CAREER_COPILOT_FIRECRAWL_API_KEY": "test-only-key",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)

    assert settings.environment == "production"
    assert "database_url" in settings.model_fields_set
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.cors_origins == []
    assert settings.enable_resume_ai is False
    assert settings.discovery_daily_user_limit == 3
    assert settings.discovery_daily_global_limit == 20
