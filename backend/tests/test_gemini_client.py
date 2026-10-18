"""Behaviour of the Gemini structured-generation client.

Everything runs offline through the injected ``api`` seam — no SDK client
is ever constructed, no credential is read, no socket is opened.
"""

from __future__ import annotations

import json
import traceback

import httpx
import pytest
from google.genai.errors import APIError
from pydantic import BaseModel, ConfigDict, Field

from app.clients.gemini import DEFAULT_MODEL, GeminiClient, GeminiError
from tests.fake_clients import RecordingGeminiApi


class TinySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    score: int | None = Field(default=None)


def make_client(api: RecordingGeminiApi, *, model: str = "test-model") -> GeminiClient:
    return GeminiClient(model, api=api)


def test_default_model_is_the_agreed_one() -> None:
    assert DEFAULT_MODEL == "gemini-3.8-flash"


def test_construction_requires_no_credentials_and_exposes_the_model() -> None:
    client = GeminiClient(DEFAULT_MODEL, api_key=None)

    assert client.model == "gemini-3.8-flash"


def test_construction_rejects_an_empty_model() -> None:
    with pytest.raises(ValueError):
        GeminiClient("")
    with pytest.raises(ValueError):
        GeminiClient("   ")


def test_returns_the_sdk_parsed_instance_directly() -> None:
    expected = TinySpec(name="alpha", score=3)
    api = RecordingGeminiApi(parsed=expected, text='{"name": "alpha", "score": 3}')
    client = make_client(api)

    result = client.generate_structured(prompt="go", schema=TinySpec)

    assert result is expected
    assert len(api.calls) == 1


def test_validates_raw_json_when_the_sdk_left_the_response_unparsed() -> None:
    api = RecordingGeminiApi(parsed=None, text='{"name": "beta", "score": 7}')
    client = make_client(api)

    result = client.generate_structured(prompt="go", schema=TinySpec)

    assert isinstance(result, TinySpec)
    assert result.name == "beta"
    assert result.score == 7


def test_a_wrongly_typed_parsed_value_falls_back_to_text_validation() -> None:
    api = RecordingGeminiApi(parsed="not a model", text='{"name": "gamma"}')
    client = make_client(api)

    result = client.generate_structured(prompt="go", schema=TinySpec)

    assert isinstance(result, TinySpec)
    assert result.name == "gamma"
    assert result.score is None


def test_requests_json_output_with_the_given_schema_and_instruction() -> None:
    api = RecordingGeminiApi(text='{"name": "delta"}')
    client = make_client(api, model="gemini-custom")

    client.generate_structured(
        prompt="analyze this", schema=TinySpec, system_instruction="be exact"
    )

    call = api.calls[0]
    assert call["model"] == "gemini-custom"
    assert call["contents"] == "analyze this"
    config = call["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema is None
    assert config.response_json_schema == TinySpec.model_json_schema()
    assert config.system_instruction == "be exact"


def test_the_schema_is_sent_as_a_raw_json_schema_dict() -> None:
    """Regression: google-genai 1.75 must never receive ``response_schema``.

    Passing the Pydantic class makes the SDK serialize snake_case
    ``additional_properties`` onto the wire and the Gemini API answers 400
    INVALID_ARGUMENT (Unknown name "additional_properties"). The raw dict
    built by ``model_json_schema()`` travels untouched as camelCase
    ``responseJsonSchema``.
    """
    api = RecordingGeminiApi(text='{"name": "delta"}')
    client = make_client(api)

    client.generate_structured(prompt="go", schema=TinySpec)

    config = api.calls[0]["config"]
    raw = config.response_json_schema
    assert isinstance(raw, dict)
    assert raw == TinySpec.model_json_schema()
    assert raw["additionalProperties"] is False
    assert raw["type"] == "object"
    assert config.response_schema is None


def test_empty_response_raises_a_structured_error() -> None:
    api = RecordingGeminiApi(parsed=None, text=None)
    client = make_client(api)

    with pytest.raises(GeminiError, match="unusable structured output"):
        client.generate_structured(prompt="go", schema=TinySpec)


def test_whitespace_response_raises_a_structured_error() -> None:
    api = RecordingGeminiApi(parsed=None, text="   ")
    client = make_client(api)

    with pytest.raises(GeminiError, match="unusable structured output"):
        client.generate_structured(prompt="go", schema=TinySpec)


def test_non_json_response_raises_without_echoing_response_text() -> None:
    secret = "provider-html-secret"
    api = RecordingGeminiApi(parsed=None, text=f"<html>{secret}</html>")
    client = make_client(api)

    with pytest.raises(GeminiError, match="unusable structured output") as info:
        client.generate_structured(prompt="go", schema=TinySpec)

    assert info.value.error_category == "structured_output_error"
    assert secret not in "".join(traceback.format_exception(info.value))


def test_json_that_violates_the_schema_raises_without_echoing_response() -> None:
    sentinel = "provider-response-secret"
    api = RecordingGeminiApi(
        parsed=None,
        text=json.dumps({"name": 123, "score": sentinel}),
    )
    client = make_client(api)

    with pytest.raises(GeminiError) as info:
        client.generate_structured(prompt="go", schema=TinySpec)

    error = info.value
    rendered = "".join(traceback.format_exception(error))
    assert error.error_category == "structured_output_error"
    assert error.http_status is None
    assert error.provider_status is None
    assert sentinel not in rendered
    assert error.__cause__ is not None
    assert error.__context__ is None


def test_api_error_keeps_only_validated_provider_metadata() -> None:
    sentinels = (
        "sensitive-provider-message",
        "response-body-secret",
        "api-key-secret",
    )
    api_error = APIError(
        429,
        {
            "error": {
                "status": "RESOURCE_EXHAUSTED",
                "message": sentinels[0],
                "debug": sentinels[1],
                "key": sentinels[2],
            }
        },
    )
    api = RecordingGeminiApi(error=api_error)
    client = make_client(api)

    with pytest.raises(GeminiError) as info:
        client.generate_structured(prompt="go", schema=TinySpec)

    error = info.value
    rendered = "".join(traceback.format_exception(error))
    assert error.error_category == "provider_api_error"
    assert error.http_status == 429
    assert error.provider_status == "RESOURCE_EXHAUSTED"
    assert isinstance(error.__cause__, RuntimeError)
    assert error.__context__ is None
    assert all(secret not in rendered for secret in sentinels)


def test_unallowlisted_provider_status_and_invalid_http_code_are_dropped() -> None:
    original = APIError(
        700,
        {
            "status": "RESOURCE_EXHAUSTED_PROVIDER_KEY",
            "message": "private provider detail",
        },
    )
    api = RecordingGeminiApi(error=original)
    client = make_client(api)

    with pytest.raises(GeminiError) as info:
        client.generate_structured(prompt="go", schema=TinySpec)

    assert info.value.error_category == "provider_api_error"
    assert info.value.http_status is None
    assert info.value.provider_status is None
    assert "private provider detail" not in str(info.value)


def test_existing_gemini_error_is_normalized_without_leaking_its_message() -> None:
    original = GeminiError("private provider detail")
    api = RecordingGeminiApi(error=original)
    client = make_client(api)

    with pytest.raises(GeminiError) as info:
        client.generate_structured(prompt="go", schema=TinySpec)

    assert info.value is not original
    assert info.value.error_category == "unknown_error"
    assert "private provider detail" not in str(info.value)


def test_empty_prompt_is_rejected_before_any_request() -> None:
    api = RecordingGeminiApi(text="{}")
    client = make_client(api)

    with pytest.raises(ValueError, match="prompt"):
        client.generate_structured(prompt="  ", schema=TinySpec)
    assert api.calls == []


def test_schema_must_be_a_pydantic_model() -> None:
    api = RecordingGeminiApi(text="{}")
    client = make_client(api)

    with pytest.raises(ValueError, match="schema"):
        client.generate_structured(prompt="go", schema=dict)  # type: ignore[arg-type]
    assert api.calls == []


def test_the_sdk_client_is_built_lazily_and_stays_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK client must outlive the call that builds it.

    ``google.genai.Client.__del__`` closes the shared HTTP transport, so a
    client that is built and then dropped breaks every later request with
    "Cannot send a request, as the client has been closed."
    """
    import gc
    from types import SimpleNamespace

    from google import genai

    stats = {"built": 0}

    class FakeTransport:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FakeModels:
        def __init__(self, transport: FakeTransport) -> None:
            self._transport = transport

        def generate_content(self, *, model: str, contents: str, config: object) -> object:
            if self._transport.closed:
                raise RuntimeError(
                    "Cannot send a request, as the client has been closed."
                )
            return SimpleNamespace(
                parsed=TinySpec(name="gamma", score=1), text=None
            )

    class FakeClient:
        def __init__(
            self, *, api_key: str | None = None, http_options: object = None
        ) -> None:
            stats["built"] += 1
            self._transport = FakeTransport()
            self.models = FakeModels(self._transport)

        def close(self) -> None:
            self._transport.close()

        def __del__(self) -> None:
            try:
                self.close()
            except Exception:
                pass

    monkeypatch.setattr(genai, "Client", FakeClient)

    client = GeminiClient("test-model", api_key="unused")
    assert stats["built"] == 0

    first = client.generate_structured(prompt="go", schema=TinySpec)
    gc.collect()
    second = client.generate_structured(prompt="go", schema=TinySpec)

    assert stats["built"] == 1
    assert isinstance(first, TinySpec)
    assert isinstance(second, TinySpec)


def test_the_sdk_client_receives_a_bounded_timeout_and_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lazily built SDK client must carry the request budget.

    ``HttpOptions.timeout`` is milliseconds (the SDK converts to seconds
    on the wire) and ``HttpRetryOptions.attempts`` counts the initial
    call, so the values bound the worst case of a synchronous agent run.
    """
    from google import genai
    from google.genai import types

    captured: dict[str, object] = {}

    class RecordingClient:
        def __init__(
            self, *, api_key: str | None = None, http_options: object = None
        ) -> None:
            captured["api_key"] = api_key
            captured["http_options"] = http_options
            self.models = object()

        def close(self) -> None:
            pass

    monkeypatch.setattr(genai, "Client", RecordingClient)

    client = GeminiClient("test-model", api_key="unused")
    client._sdk_models()

    options = captured["http_options"]
    assert isinstance(options, types.HttpOptions)
    assert options.timeout == GeminiClient.GEMINI_REQUEST_TIMEOUT_SECONDS * 1000
    assert options.timeout == 120_000
    assert options.retry_options is not None
    assert (
        options.retry_options.attempts == GeminiClient.GEMINI_REQUEST_MAX_ATTEMPTS
    )
    assert options.retry_options.attempts == 1


def test_the_real_sdk_client_wires_the_configured_http_options() -> None:
    """The real SDK accepts the options and bounds its tenacity retries.

    Constructing the client builds an HTTP transport but opens no
    connection, and nothing here sends a request.
    """
    client = GeminiClient(DEFAULT_MODEL, api_key="offline-test-key")
    try:
        models = client._sdk_models()
        assert models is client._sdk_client.models

        api_client = client._sdk_client._api_client
        assert api_client._http_options.timeout == (
            GeminiClient.GEMINI_REQUEST_TIMEOUT_SECONDS * 1000
        )
        assert api_client._http_options.retry_options is not None
        assert (
            api_client._http_options.retry_options.attempts
            == GeminiClient.GEMINI_REQUEST_MAX_ATTEMPTS
        )
        assert (
            api_client._retry.stop.max_attempt_number
            == GeminiClient.GEMINI_REQUEST_MAX_ATTEMPTS
        )
    finally:
        if client._sdk_client is not None:
            client._sdk_client.close()


def test_a_timeout_failure_is_classified_and_chained_safely() -> None:
    api = RecordingGeminiApi(error=TimeoutError("timeout message secret"))
    client = make_client(api)

    with pytest.raises(GeminiError) as info:
        client.generate_structured(prompt="go", schema=TinySpec)

    assert info.value.error_category == "transport_error"
    assert isinstance(info.value.__cause__, RuntimeError)
    assert "timeout message secret" not in "".join(
        traceback.format_exception(info.value)
    )


def test_httpx_transport_failure_is_classified_without_exception_text() -> None:
    api = RecordingGeminiApi(error=httpx.ConnectError("transport secret"))
    client = make_client(api)

    with pytest.raises(GeminiError) as info:
        client.generate_structured(prompt="go", schema=TinySpec)

    assert info.value.error_category == "transport_error"
    assert info.value.http_status is None
    assert "transport secret" not in "".join(
        traceback.format_exception(info.value)
    )


def test_unknown_exception_has_generic_diagnostics_and_safe_chain() -> None:
    api = RecordingGeminiApi(error=RuntimeError("unknown sensitive detail"))
    client = make_client(api)

    with pytest.raises(GeminiError) as info:
        client.generate_structured(prompt="go", schema=TinySpec)

    assert info.value.error_category == "unknown_error"
    assert info.value.http_status is None
    assert info.value.provider_status is None
    assert "unknown sensitive detail" not in "".join(
        traceback.format_exception(info.value)
    )


def test_a_wrongly_typed_parsed_value_with_invalid_text_raises() -> None:
    api = RecordingGeminiApi(parsed={"name": "delta"}, text="oops")
    client = make_client(api)

    with pytest.raises(GeminiError, match="unusable structured output"):
        client.generate_structured(prompt="go", schema=TinySpec)


def test_a_wrongly_typed_parsed_value_with_schema_violating_text_raises() -> None:
    api = RecordingGeminiApi(parsed="not a model", text='{"score": "high"}')
    client = make_client(api)

    with pytest.raises(GeminiError, match="unusable structured output"):
        client.generate_structured(prompt="go", schema=TinySpec)
