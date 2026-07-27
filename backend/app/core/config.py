from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "AI Career Copilot API"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    database_url: str = Field(
        default="sqlite:///./career_copilot.db",
        description="SQLAlchemy database URL.",
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
