"""Tests for extract_finish_reasons() — the gen_ai.response.finish_reasons fallback helper.

Two assertions per phase-3-implementation.md § Step 8:

1. Negative case — a production ``LLMResponse`` (no ``finish_reasons``
   field) returns ``None`` so the caller omits the attribute entirely
   rather than fabricating a sentinel value.
2. Positive case — a ``SimpleNamespace`` (or any duck-typed object)
   exposing ``finish_reasons`` returns the value verbatim. Guards
   against an inverted-predicate bug the negative case alone would not
   catch.
"""

from __future__ import annotations

import types


def test_extract_finish_reasons_returns_none_for_production_llm_response() -> None:
    """LLMResponse with finish_reason unset (None default) → helper returns None.

    Post-GH-321: LLMResponse declares `finish_reason` (singular) but defaults
    to None when the backend doesn't emit a value. The helper still returns
    None in that case — equivalent to the pre-GH-321 behavior when the
    plural field was entirely absent.
    """
    from samantha_server.llm.client import LLMResponse
    from samantha_server.observability.otel import extract_finish_reasons

    response = LLMResponse(
        text="ok",
        input_tokens=10,
        output_tokens=20,
        model_id="test-model",
        latency_us=1000,
    )
    assert extract_finish_reasons(response) is None


def test_extract_finish_reasons_returns_value_when_field_present() -> None:
    """Duck-typed object with finish_reasons returns the value verbatim."""
    from samantha_server.observability.otel import extract_finish_reasons

    fake = types.SimpleNamespace(
        text="ok",
        input_tokens=10,
        output_tokens=20,
        model_id="test-model",
        latency_us=1000,
        finish_reasons=["stop"],
    )
    assert extract_finish_reasons(fake) == ["stop"]


def test_extract_finish_reasons_returns_none_when_attribute_set_to_none() -> None:
    """An object whose finish_reasons is explicitly None is treated as 'not reported'."""
    from samantha_server.observability.otel import extract_finish_reasons

    fake = types.SimpleNamespace(finish_reasons=None)
    assert extract_finish_reasons(fake) is None
