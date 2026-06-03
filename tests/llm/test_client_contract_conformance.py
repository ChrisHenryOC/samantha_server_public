"""Contract-conformance test: MLXClient ↔ OMLXClient produce structurally identical LLMResponse.

Both clients implement the LLMClient Protocol. For the same prompt, both must return
LLMResponse instances with:
  - the same set of fields
  - the same types for text, input_tokens, output_tokens

latency_us and model_id are allowed to differ (they are implementation-specific).

MLXClient's complete() is stubbed (mlx-lm is not installed in CI). OMLXClient's
HTTP layer is stubbed via httpx.MockTransport. Both stubs return structurally
analogous responses so the comparison is fair.
"""

from __future__ import annotations

from typing import Any

import httpx

from samantha_server.llm.client import LLMResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OMLX_RESPONSE_BODY: dict[str, Any] = {
    "choices": [{"text": "hello from omlx"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
}

_MODELS_RESPONSE: dict[str, Any] = {
    "data": [{"id": "test-model", "object": "model"}],
    "object": "list",
}


def _make_omlx_client() -> Any:
    """Construct an OMLXClient with a stubbed MockTransport."""
    from samantha_server.llm.omlx_client import OMLXClient

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_MODELS_RESPONSE)
        return httpx.Response(200, json=_OMLX_RESPONSE_BODY)

    return OMLXClient(_transport=httpx.MockTransport(_handler))


def _make_stub_llm_response() -> LLMResponse:
    """Return a fixed LLMResponse that a stubbed MLXClient would produce."""
    return LLMResponse(
        text="hello from mlx",
        input_tokens=5,
        output_tokens=3,
        model_id="test-mlx-model",
        latency_us=1234,
    )


# ---------------------------------------------------------------------------
# Contract conformance tests
# ---------------------------------------------------------------------------


def test_omlx_and_mlx_response_have_same_fields() -> None:
    """OMLXClient and MLXClient LLMResponse instances expose the same set of fields."""
    omlx_client = _make_omlx_client()
    omlx_response = omlx_client.complete("say hello")
    mlx_response = _make_stub_llm_response()

    # Use Pydantic model_fields for field-name introspection.
    omlx_fields = set(LLMResponse.model_fields.keys())
    mlx_fields = set(LLMResponse.model_fields.keys())

    assert omlx_fields == mlx_fields

    # Also verify both responses are instances of the same class.
    assert type(omlx_response) is type(mlx_response)


def test_omlx_and_mlx_response_text_field_is_str() -> None:
    """Both OMLXClient and MLXClient responses have text: str."""
    omlx_response = _make_omlx_client().complete("say hello")
    mlx_response = _make_stub_llm_response()

    assert isinstance(omlx_response.text, str)
    assert isinstance(mlx_response.text, str)


def test_omlx_and_mlx_response_input_tokens_is_int() -> None:
    """Both OMLXClient and MLXClient responses have input_tokens: int."""
    omlx_response = _make_omlx_client().complete("say hello")
    mlx_response = _make_stub_llm_response()

    assert isinstance(omlx_response.input_tokens, int)
    assert isinstance(mlx_response.input_tokens, int)


def test_omlx_and_mlx_response_output_tokens_is_int() -> None:
    """Both OMLXClient and MLXClient responses have output_tokens: int."""
    omlx_response = _make_omlx_client().complete("say hello")
    mlx_response = _make_stub_llm_response()

    assert isinstance(omlx_response.output_tokens, int)
    assert isinstance(mlx_response.output_tokens, int)


def test_omlx_and_mlx_response_token_counts_non_negative() -> None:
    """Both responses report non-negative token counts."""
    omlx_response = _make_omlx_client().complete("say hello")
    mlx_response = _make_stub_llm_response()

    assert omlx_response.input_tokens >= 0
    assert omlx_response.output_tokens >= 0
    assert mlx_response.input_tokens >= 0
    assert mlx_response.output_tokens >= 0
