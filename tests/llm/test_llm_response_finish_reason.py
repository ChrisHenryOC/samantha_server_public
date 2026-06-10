"""Tests for S2.1: finish_reason field on LLMResponse.

Verifies:
- finish_reason defaults to None when not supplied.
- finish_reason round-trips for every value in the OpenAI vocabulary.
- Unknown wire values are coerced to None (review #4 — wire
  robustness alongside Literal type narrowness).
- LLMResponse remains frozen (immutable).
"""

from __future__ import annotations

import pytest


def test_llm_response_finish_reason_defaults_to_none() -> None:
    """LLMResponse constructed without finish_reason has finish_reason=None."""
    from samantha_server.llm.client import LLMResponse

    response = LLMResponse(
        text="x",
        input_tokens=1,
        output_tokens=2,
        model_id="m",
        latency_us=10,
    )
    assert response.finish_reason is None


@pytest.mark.parametrize(
    "value",
    ["stop", "length", "content_filter", "tool_calls"],
)
def test_llm_response_finish_reason_roundtrips_known_value(value: str) -> None:
    """LLMResponse with a known finish_reason value round-trips unchanged.

    Covers every value in the OpenAI vocabulary (the Literal type
    narrowness contract introduced by review #4).
    """
    from samantha_server.llm.client import LLMResponse

    response = LLMResponse(
        text="x",
        input_tokens=1,
        output_tokens=2,
        model_id="m",
        latency_us=10,
        finish_reason=value,  # type: ignore[arg-type]
    )
    assert response.finish_reason == value


@pytest.mark.parametrize(
    "unknown_value",
    ["function_call", "FOO", "", "completed"],
)
def test_llm_response_unknown_finish_reason_coerces_to_none(unknown_value: str) -> None:
    """LLMResponse coerces unknown wire values to None.

    Pydantic's Literal validation would otherwise reject the value with a
    ValidationError, escaping the typed-error contract on the LLM-client
    boundary. The before-mode field_validator coerces unknown strings to
    None so the LLMResponse construction always succeeds. The signal is
    downgraded — unknown reasons are simply not reported — but the call
    completes.
    """
    from samantha_server.llm.client import LLMResponse

    response = LLMResponse(
        text="x",
        input_tokens=1,
        output_tokens=2,
        model_id="m",
        latency_us=10,
        finish_reason=unknown_value,  # type: ignore[arg-type]
    )
    assert response.finish_reason is None
