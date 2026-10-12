from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "AI Career Copilot API"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    database_url: str = Field(
        default="postgresql+psycopg://career_copilot:career_copilot@localhost:5432/career_copilot",
        description="SQLAlchemy database URL.",
    )
    searxng_base_url: str = Field(
        default="http://localhost:8080",
        description="Base URL of the SearXNG search instance used by discovery.",
    )
    firecrawl_base_url: str = Field(
        default="http://localhost:3002",
        description="Base URL of the Firecrawl API used by discovery.",
    )
    firecrawl_api_key: str | None = Field(
        default=None,
        description="Optional Firecrawl API key; sent as a bearer token when set.",
    )
    job_freshness_days: int = Field(
        default=30,
        ge=1,
        description=(
            "Age in days after which a verified-available job is presented as "
            "stale rather than available. Stale means not recently verified, "
            "never closed."
        ),
    )
    job_verification_cooldown_seconds: int = Field(
        default=60,
        ge=0,
        description=(
            "Minimum seconds between explicit re-verifications of the same job. "
            "A value of 0 disables the per-job cooldown."
        ),
    )
    gemini_api_key: str | None = Field(
        default=None,
        description="Google Gemini API key used by the Job Analysis agent.",
    )
    gemini_model: str = Field(
        default="gemini-3.8-flash",
        description="Gemini model used for structured job analysis.",
    )
    cors_origins: list[str] = Field(
        default=DEFAULT_CORS_ORIGINS,
        description=(
            "Origins allowed to call the API from a browser. Empty disables "
            "cross-origin access entirely; production origins must be set "
            "explicitly. JSON list when supplied as an environment variable."
        ),
    )
    session_cookie_name: str = Field(
        default="career_copilot_session",
        description="Name of the HttpOnly session cookie.",
    )
    csrf_cookie_name: str = Field(
        default="career_copilot_csrf",
        description="Name of the readable CSRF token cookie.",
    )
    session_ttl_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        ge=60,
        description="Absolute lifetime of a login session, in seconds.",
    )
    session_cookie_secure: bool = Field(
        default=False,
        description=(
            "Set the Secure flag on auth cookies. Must be true behind HTTPS "
            "in production; false so local http development works."
        ),
    )
    session_cookie_samesite: str = Field(
        default="lax",
        description="SameSite policy for auth cookies (lax, strict or none).",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CAREER_COPILOT_",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
