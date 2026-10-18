from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "AI Career Copilot API"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    search_provider: Literal["searxng", "firecrawl"] = Field(
        default="searxng",
        description="Search API used by job discovery; SearXNG remains the local default.",
    )
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
    discovery_daily_user_limit: int = Field(
        default=100,
        ge=1,
        le=10_000,
        description=(
            "Daily provider-backed operations allowed per authenticated user: "
            "one unit per discovery run or job-verification attempt."
        ),
    )
    discovery_daily_global_limit: int = Field(
        default=1_000,
        ge=1,
        le=100_000,
        description=(
            "Shared daily provider-backed operation ceiling across users; "
            "one unit per discovery run or job-verification attempt."
        ),
    )
    enable_resume_ai: bool = Field(
        default=True,
        description=(
            "Allow resume upload and resume-based Gemini features. Production "
            "defaults to disabled unless explicitly enabled."
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
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def validate_production_configuration(self) -> "Settings":
        """Reject unsafe hosted settings without echoing secret values."""
        if self.search_provider == "firecrawl" and not (
            self.firecrawl_api_key and self.firecrawl_api_key.strip()
        ):
            raise ValueError(
                "CAREER_COPILOT_FIRECRAWL_API_KEY is required when search provider is firecrawl"
            )

        if self.environment.casefold() != "production":
            return self

        # Development stays convenient, while a hosted service does not
        # process resumes or spend the free provider allowance by default.
        if "enable_resume_ai" not in self.model_fields_set:
            self.enable_resume_ai = False
        if "discovery_daily_user_limit" not in self.model_fields_set:
            self.discovery_daily_user_limit = 3
        if "discovery_daily_global_limit" not in self.model_fields_set:
            self.discovery_daily_global_limit = 20

        if self.debug:
            raise ValueError("CAREER_COPILOT_DEBUG must be false in production")
        if not self.session_cookie_secure:
            raise ValueError(
                "CAREER_COPILOT_SESSION_COOKIE_SECURE must be true in production"
            )

        if self.search_provider == "firecrawl":
            if "firecrawl_base_url" not in self.model_fields_set:
                raise ValueError(
                    "CAREER_COPILOT_FIRECRAWL_BASE_URL must be explicitly configured in production"
                )
            if not _is_https_service_url(self.firecrawl_base_url):
                raise ValueError(
                    "CAREER_COPILOT_FIRECRAWL_BASE_URL must be an HTTPS service URL in production"
                )
        else:
            if "searxng_base_url" not in self.model_fields_set:
                raise ValueError(
                    "CAREER_COPILOT_SEARXNG_BASE_URL must be explicitly configured in production"
                )
            if not _is_https_service_url(self.searxng_base_url):
                raise ValueError(
                    "CAREER_COPILOT_SEARXNG_BASE_URL must be an HTTPS service URL in production"
                )

        if "database_url" not in self.model_fields_set or not self.database_url.strip():
            raise ValueError(
                "CAREER_COPILOT_DATABASE_URL must be explicitly configured in production"
            )

        try:
            database_url = urlsplit(self.database_url)
            valid_database_port = _has_valid_url_port(database_url)
            sslmode = parse_qs(database_url.query).get("sslmode", [""])[0].casefold()
        except ValueError:
            raise ValueError(
                "CAREER_COPILOT_DATABASE_URL must be a PostgreSQL URL with sslmode=require or stronger"
            ) from None
        if (
            database_url.scheme not in {"postgresql", "postgresql+psycopg"}
            or not database_url.hostname
            or not valid_database_port
            or sslmode not in {"require", "verify-ca", "verify-full"}
        ):
            raise ValueError(
                "CAREER_COPILOT_DATABASE_URL must be a PostgreSQL URL with sslmode=require or stronger"
            )
        if database_url.scheme == "postgresql":
            # Neon provides a standard libpq URL; use the psycopg 3 driver
            # already installed by this repository rather than psycopg2.
            self.database_url = self.database_url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )

        if "cors_origins" not in self.model_fields_set:
            self.cors_origins = []
        else:
            local_hosts = {"localhost", "127.0.0.1", "::1"}
            for origin in self.cors_origins:
                try:
                    parsed_origin = urlsplit(origin)
                    origin_host = parsed_origin.hostname
                except ValueError:
                    origin_host = None
                    parsed_origin = None
                if (
                    parsed_origin is None
                    or parsed_origin.scheme not in {"http", "https"}
                    or not origin_host
                    or parsed_origin.username is not None
                    or parsed_origin.password is not None
                    or parsed_origin.path not in {"", "/"}
                    or parsed_origin.query
                    or parsed_origin.fragment
                ):
                    raise ValueError(
                        "CAREER_COPILOT_CORS_ORIGINS must contain valid origin URLs"
                    )
                if origin_host in local_hosts:
                    raise ValueError(
                        "CAREER_COPILOT_CORS_ORIGINS must not include development origins in production"
                    )
        return self


def _is_https_service_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and _has_valid_url_port(parsed)
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def _has_valid_url_port(parsed) -> bool:
    """Reject malformed, empty, zero, and out-of-range explicit URL ports."""
    if parsed.netloc.endswith(":"):
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    return port is None or 1 <= port <= 65_535


@lru_cache
def get_settings() -> Settings:
    return Settings()
