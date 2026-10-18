from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

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
    from app.clients.search import SearchClient

DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class _SPAStaticFiles(StaticFiles):
    """Serve the Vite entrypoint for browser routes, never for API paths."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            if path == "api" or path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            if scope["method"] not in {"GET", "HEAD"}:
                raise
            return await super().get_response("index.html", scope)


def create_app(
    *,
    searxng: SearchClient | None = None,
    firecrawl: FirecrawlClient | None = None,
    gemini: GeminiClient | None = None,
    frontend_dist_dir: str | Path | None = None,
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

    static_dir: Path | None = None
    if frontend_dist_dir is not None:
        static_dir = Path(frontend_dist_dir)
    elif settings.environment.casefold() == "production":
        static_dir = DEFAULT_FRONTEND_DIST

    if static_dir is not None:
        if not (static_dir / "index.html").is_file():
            raise RuntimeError("frontend build output is missing or incomplete")

        def unknown_api_path() -> JSONResponse:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

        api_not_found_methods = [
            "GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"
        ]
        app.add_api_route(
            "/api", unknown_api_path,
            methods=api_not_found_methods,
            include_in_schema=False,
        )
        app.add_api_route(
            "/api/{path:path}", unknown_api_path,
            methods=api_not_found_methods,
            include_in_schema=False,
        )
        # StaticFiles resolves paths beneath its configured directory and
        # serves index.html for client-side routes when html=True.
        app.mount(
            "/",
            _SPAStaticFiles(directory=str(static_dir), html=True),
            name="frontend",
        )

    return app


app = create_app()
