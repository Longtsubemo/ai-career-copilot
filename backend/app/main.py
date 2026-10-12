from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.cover_letter import register_cover_letter_handler
from app.agents.discovery import register_discovery_handler
from app.agents.job_analysis import register_job_analysis_handler
from app.agents.job_matching import register_job_matching_handler
from app.agents.runtime import HandlerRegistry
from app.api.csrf import CSRFMiddleware
from app.api.errors import register_exception_handlers
from app.api.router import api_router
from app.clients.firecrawl import FirecrawlClient
from app.core.config import get_settings
from app.core.logging import configure_logging

if TYPE_CHECKING:
    from app.clients.gemini import GeminiClient
    from app.clients.searxng import SearXNGClient


def create_app(
    *,
    searxng: SearXNGClient | None = None,
    firecrawl: FirecrawlClient | None = None,
    gemini: GeminiClient | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    The optional clients are the injection seam used by tests: supplying
    fakes builds a fully working app that reaches no external service,
    while the defaults construct settings-derived clients exactly as
    before. A fresh handler registry is built per app, so two apps in one
    process never share registration state.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        version=settings.app_version,
    )
    app.include_router(api_router)
    register_exception_handlers(app)

    app.add_middleware(CSRFMiddleware)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    registry = HandlerRegistry()
    firecrawl_client = firecrawl or FirecrawlClient(
        settings.firecrawl_base_url, api_key=settings.firecrawl_api_key
    )
    register_discovery_handler(registry, searxng=searxng, firecrawl=firecrawl_client)
    register_job_analysis_handler(registry, gemini=gemini)
    register_job_matching_handler(registry, gemini=gemini)
    register_cover_letter_handler(registry, gemini=gemini)
    app.state.agent_registry = registry
    app.state.firecrawl = firecrawl_client

    return app


app = create_app()
