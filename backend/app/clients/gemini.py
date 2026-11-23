"""Client for Google's Gemini structured generation.

All Google GenAI SDK details — client construction, model selection,
response-schema configuration, response extraction and validation — live
here. Agents depend only on :meth:`GeminiClient.generate_structured`,
which accepts a prompt plus a Pydantic schema and returns an instance of
that schema or raises :class:`GeminiError`.

The SDK client is built lazily on the first call, so constructing a
``GeminiClient`` never requires credentials and never touches the network.
An injectable ``api`` seam (anything with ``generate_content``) lets tests
run completely offline.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

import httpx
from google.genai.errors import APIError
from google.genai import types
from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = "gemini-3.8-flash"


_ERROR_CATEGORIES = frozenset(
    {
        "provider_api_error",
        "transport_error",
        "structured_output_error",
        "unknown_error",
    }
)
_PROVIDER_STATUS_ALLOWLIST = frozenset(
    {
        "ABORTED",
        "ALREADY_EXISTS",
        "CANCELLED",
        "DATA_LOSS",
        "DEADLINE_EXCEEDED",
        "FAILED_PRECONDITION",
        "INTERNAL",
        "INVALID_ARGUMENT",
        "NOT_FOUND",
        "OUT_OF_RANGE",
        "PERMISSION_DENIED",
        "RESOURCE_EXHAUSTED",
        "UNAUTHENTICATED",
        "UNAVAILABLE",
        "UNIMPLEMENTED",
        "UNKNOWN",
    }
)
_SAFE_ERROR_MESSAGES = {
    "provider_api_error": "Gemini provider API request failed.",
    "transport_error": "Gemini transport request failed.",
    "structured_output_error": "Gemini returned unusable structured output.",
    "unknown_error": "Gemini request failed.",
}


def _safe_error_category(value: object) -> str:
    if isinstance(value, str) and value in _ERROR_CATEGORIES:
        return value
    return "unknown_error"


def _safe_http_status(value: object) -> int | None:
    if type(value) is int and 100 <= value <= 599:
        return value
    return None


def _safe_provider_status(value: object) -> str | None:
    if isinstance(value, str) and value in _PROVIDER_STATUS_ALLOWLIST:
        return value
    return None


class _SanitizedGeminiCause(RuntimeError):
    """Safe exception-chain marker that cannot retain SDK response details."""

    def __init__(self, error_category: str) -> None:
        category = _safe_error_category(error_category)
        super().__init__(f"Gemini failure category: {category}")


class GeminiError(RuntimeError):
    """A Gemini failure with bounded, safe diagnostic metadata.

    The optional positional message is accepted for source compatibility but
    deliberately discarded: SDK exception strings can contain response
    bodies, user content, or other provider details.
    """

    def __init__(
        self,
        message: str | None = None,
        *,
        error_category: str = "unknown_error",
        http_status: object = None,
        provider_status: object = None,
    ) -> None:
        del message
        self.error_category = _safe_error_category(error_category)
        self.http_status = _safe_http_status(http_status)
        self.provider_status = _safe_provider_status(provider_status)
        super().__init__(_SAFE_ERROR_MESSAGES[self.error_category])

    def diagnostic_fields(self) -> dict[str, str | int | None]:
        """Return only validated metadata suitable for a local artifact."""
        return {
            "error_category": _safe_error_category(self.error_category),
            "http_status": _safe_http_status(self.http_status),
            "provider_status": _safe_provider_status(self.provider_status),
        }


class GeminiClient:
    """Small structured-generation client around the Google GenAI SDK.

    ``api`` is injectable so tests can drive the client with a scripted
    stand-in instead of the real SDK and network.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        api: Any | None = None,
    ) -> None:
        if not model or not model.strip():
            raise ValueError("model must not be empty")
        self._model = model
        self._api_key = api_key
        self._api = api
        self._sdk_client = None

    @property
    def model(self) -> str:
        return self._model

    def generate_structured(
        self,
        prompt: str,
        schema: type[BaseModel],
        system_instruction: str | None = None,
    ) -> BaseModel:
        """Generate JSON constrained to ``schema`` and return a validated instance.

        The schema is sent through ``response_json_schema`` as a raw JSON
        Schema dict built by ``model_json_schema()``: passing the Pydantic
        class to ``response_schema`` makes google-genai 1.75 serialize it as
        snake_case ``additional_properties``, which the Gemini API rejects
        with 400 INVALID_ARGUMENT.

        Raises :class:`GeminiError` on any transport/API failure, on an
        empty response, or when the response does not validate against
        ``schema``. The SDK itself silently leaves invalid payloads
        unparsed, so validation is repeated here and always surfaced.
        """
        if not prompt or not prompt.strip():
            raise ValueError("prompt must not be empty")
        if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
            raise ValueError("schema must be a pydantic BaseModel subclass")

        failure: GeminiError | None = None
        try:
            models = self._api if self._api is not None else self._sdk_models()
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_json_schema=schema.model_json_schema(),
            )
            response = models.generate_content(
                model=self._model,
                contents=prompt,
                config=config,
            )
        except Exception as exc:
            failure = _failure_from_exception(exc)

        if failure is not None:
            # Raise outside the except block so the original provider
            # exception is not retained as __context__ or exposed in a
            # traceback. Keep a sanitized cause for normal exception chaining.
            raise failure from _SanitizedGeminiCause(failure.error_category)

        return _extract_structured(response, schema)

    GEMINI_REQUEST_TIMEOUT_SECONDS = 120
    GEMINI_REQUEST_MAX_ATTEMPTS = 1

    def _sdk_models(self) -> Any:
        """Build the real SDK client lazily, on first use only, and reuse it.

        The SDK's ``Client.__del__`` closes the underlying HTTP transport,
        so the client must stay referenced for as long as requests are
        made through the returned ``models`` handle.

        The client carries the request timeout (in milliseconds, as the
        SDK expects) and exactly one HTTP attempt: ``AgentRuntime`` owns
        application-level retries, so SDK-level retries would multiply
        every runtime attempt and stall the synchronous agent endpoints.
        """
        if self._sdk_client is None:
            from google import genai

            self._sdk_client = genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(
                    timeout=self.GEMINI_REQUEST_TIMEOUT_SECONDS * 1000,
                    retry_options=types.HttpRetryOptions(
                        attempts=self.GEMINI_REQUEST_MAX_ATTEMPTS
                    ),
                ),
            )
        return self._sdk_client.models


def _extract_structured(response: Any, schema: type[BaseModel]) -> BaseModel:
    """Return the response as a validated ``schema`` instance or raise."""
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return parsed

    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        _raise_structured_output_error()

    invalid_json = False
    try:
        payload = json.loads(text)
    except ValueError:
        invalid_json = True
        payload = None
    if invalid_json:
        _raise_structured_output_error()

    invalid_schema = False
    try:
        result = schema.model_validate(payload)
    except ValidationError:
        invalid_schema = True
        result = None
    if invalid_schema:
        _raise_structured_output_error()
    return result


def _failure_from_exception(exc: Exception) -> GeminiError:
    """Map SDK/transport exceptions to safe categories and allowlisted fields."""
    if isinstance(exc, APIError):
        return GeminiError(
            error_category="provider_api_error",
            http_status=getattr(exc, "code", None),
            provider_status=getattr(exc, "status", None),
        )
    if isinstance(exc, GeminiError):
        return GeminiError(**exc.diagnostic_fields())
    if isinstance(
        exc,
        (httpx.TransportError, TimeoutError, ConnectionError, OSError),
    ):
        return GeminiError(error_category="transport_error")
    return GeminiError(error_category="unknown_error")


def _raise_structured_output_error() -> NoReturn:
    error = GeminiError(error_category="structured_output_error")
    raise error from _SanitizedGeminiCause(error.error_category)
