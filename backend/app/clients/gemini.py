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
from typing import Any

from google.genai import types
from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = "gemini-3.8-flash"


class GeminiError(RuntimeError):
    """A Gemini request failed at transport, API or structured-output level."""


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
        except GeminiError:
            raise
        except Exception as exc:
            raise GeminiError(f"structured generation failed: {exc}") from exc

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
        raise GeminiError("Gemini returned no usable structured content")

    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise GeminiError("Gemini response is not valid JSON") from exc

    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise GeminiError(
            f"Gemini response does not match the expected schema: {exc}"
        ) from exc
