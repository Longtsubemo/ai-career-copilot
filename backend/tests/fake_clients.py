"""Deterministic stand-ins for the external tool clients.

Discovery tests never touch SearXNG, Firecrawl or any network, and job
analysis tests never touch Gemini. These fakes mirror the small surface
each agent depends on — ``search``, ``scrape`` and
``generate_structured`` — and record what they were asked for so tests
can assert on the traffic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from app.clients.firecrawl import CrawlResult, FirecrawlError
from app.clients.gemini import GeminiError
from app.clients.searxng import SearchResult, SearXNGError


class FakeSearXNGClient:
    """Scripted search client: fixed results, or a fixed failure.

    Every recorded query lets a test assert that the agent built and used
    the deterministic query string.
    """

    def __init__(
        self,
        results: Sequence[SearchResult] = (),
        *,
        error: SearXNGError | None = None,
    ) -> None:
        self._results = list(results)
        self._error = error
        self.queries: list[str] = []

    @property
    def base_url(self) -> str:
        return "http://fake-searxng.test"

    @property
    def results(self) -> list[SearchResult]:
        return list(self._results)

    def search(self, query: str, *, page: int = 1) -> list[SearchResult]:
        self.queries.append(query)
        if self._error is not None:
            raise self._error
        return list(self._results)


class FakeFirecrawlClient:
    """Scripted crawl client keyed by URL.

    A URL mapped to a :class:`CrawlResult` returns it, a URL mapped to an
    exception raises it, and an unscripted URL raises
    :class:`FirecrawlError` so missing fixtures fail loudly.
    """

    def __init__(self, pages: Mapping[str, CrawlResult | BaseException] | None = None) -> None:
        self._pages: dict[str, CrawlResult | BaseException] = dict(pages or {})
        self.requested: list[str] = []

    @property
    def base_url(self) -> str:
        return "http://fake-firecrawl.test"

    def add(self, url: str, page: CrawlResult | BaseException) -> None:
        self._pages[url] = page

    def scrape(self, url: str) -> CrawlResult:
        self.requested.append(url)
        entry = self._pages.get(url)
        if entry is None:
            raise FirecrawlError(f"no scripted page for {url}")
        if isinstance(entry, BaseException):
            raise entry
        return entry


class FakeGeminiClient:
    """Scripted agent-level Gemini stand-in for agent tests.

    Records every structured-generation call — prompt, schema and system
    instruction — then returns (or raises) the scripted result. The
    ``model`` attribute mirrors the real client's property so persisted
    model names stay assertable.
    """

    def __init__(
        self,
        result: object | None = None,
        *,
        model: str = "gemini-3.8-flash",
    ) -> None:
        self._result = result
        self._model = model
        self.calls: list[dict[str, Any]] = []

    @property
    def model(self) -> str:
        return self._model

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: type,
        system_instruction: str | None = None,
    ) -> object:
        self.calls.append(
            {
                "prompt": prompt,
                "schema": schema,
                "system_instruction": system_instruction,
            }
        )
        if isinstance(self._result, BaseException):
            raise self._result
        if self._result is None:
            raise GeminiError("fake Gemini client has no scripted result")
        return self._result


class SchemaScriptedGeminiClient:
    """Gemini stand-in answering by schema type for multi-agent flows.

    A workflow test drives analysis, matching and cover letter through one
    client: each call is answered with the result scripted for the schema
    the agent asked for, and a schema nobody scripted raises loudly
    instead of returning an object the next agent cannot use. The
    ``model`` attribute mirrors the real client's property so persisted
    model names stay assertable.
    """

    def __init__(
        self,
        results: Mapping[type, object],
        *,
        model: str = "gemini-3.8-flash",
    ) -> None:
        self._results = dict(results)
        self._model = model
        self.calls: list[dict[str, Any]] = []

    @property
    def model(self) -> str:
        return self._model

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: type,
        system_instruction: str | None = None,
    ) -> object:
        self.calls.append(
            {
                "prompt": prompt,
                "schema": schema,
                "system_instruction": system_instruction,
            }
        )
        if schema not in self._results:
            raise GeminiError(f"no scripted result for schema {schema.__name__}")
        result = self._results[schema]
        if isinstance(result, BaseException):
            raise result
        return result


class RecordingGeminiApi:
    """Scripted stand-in for the SDK's ``models`` resource.

    Used by tests that drive the real :class:`GeminiClient` offline:
    it returns one scripted response object (or raises one scripted
    error) and records every invocation for assertions on model,
    contents and configuration.
    """

    def __init__(
        self,
        *,
        parsed: object = None,
        text: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.response = SimpleNamespace(parsed=parsed, text=text)
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def generate_content(
        self, *, model: str, contents: str, config: Any
    ) -> Any:
        self.calls.append(
            {"model": model, "contents": contents, "config": config}
        )
        if self.error is not None:
            raise self.error
        return self.response


def search_result(
    url: str, title: str, content: str | None = None, engine: str | None = None
) -> SearchResult:
    """Build a :class:`SearchResult` with sensible test defaults."""
    return SearchResult(url=url, title=title, content=content, engine=engine)


def crawl_result(
    url: str,
    *,
    markdown: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> CrawlResult:
    """Build a :class:`CrawlResult` with sensible test defaults."""
    return CrawlResult(
        url=url, markdown=markdown, metadata=dict(metadata or {})
    )


def listing_page(
    url: str,
    *,
    title: str | None = None,
    description: str | None = None,
    location: str | None = None,
    company: str | None = None,
    employment_type: str | None = None,
    experience: str | None = None,
    markdown_extra: str = "",
) -> CrawlResult:
    """Build a realistic job-listing page: metadata plus markdown lines."""
    metadata: dict[str, object] = {}
    if title is not None:
        metadata["title"] = title
    if description is not None:
        metadata["description"] = description
    if location is not None:
        metadata["location"] = location
    if company is not None:
        metadata["company"] = company
    if employment_type is not None:
        metadata["employmentType"] = employment_type
    if experience is not None:
        metadata["experienceLevel"] = experience

    lines: list[str] = []
    if title is not None:
        lines.append(f"# {title}")
    if location is not None:
        lines.append(f"Location: {location}")
    if company is not None:
        lines.append(f"Company: {company}")
    if employment_type is not None:
        lines.append(f"Employment type: {employment_type}")
    if experience is not None:
        lines.append(f"Experience: {experience}")
    if markdown_extra:
        lines.append(markdown_extra)
    markdown = "\n".join(lines) if lines else None
    return CrawlResult(url=url, markdown=markdown, metadata=metadata)
