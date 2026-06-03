"""Tests for GH-321 S2.4: extract_finish_reasons() updated to support
LLMResponse.finish_reason (singular) in addition to the existing plural
finish_reasons duck-typed attribute.
"""

from __future__ import annotations

import types


def test_extract_finish_reasons_wraps_singular_finish_reason() -> None:
    """LLMResponse with finish_reason='length' → helper returns ['length']."""
    from samantha_server.llm.client import LLMResponse
    from samantha_server.observability.otel import extract_finish_reasons

    response = LLMResponse(
        text="x",
        input_tokens=1,
        output_tokens=2,
        model_id="m",
        latency_us=10,
        finish_reason="length",
    )
    assert extract_finish_reasons(response) == ["length"]


def test_extract_finish_reasons_returns_none_when_finish_reason_is_none() -> None:
    """LLMResponse with finish_reason=None → helper returns None."""
    from samantha_server.llm.client import LLMResponse
    from samantha_server.observability.otel import extract_finish_reasons

    response = LLMResponse(
        text="x",
        input_tokens=1,
        output_tokens=2,
        model_id="m",
        latency_us=10,
        finish_reason=None,
    )
    assert extract_finish_reasons(response) is None


def test_extract_finish_reasons_plural_takes_precedence() -> None:
    """Duck-typed object with plural finish_reasons=["length", "stop"] → list returned unchanged."""
    from samantha_server.observability.otel import extract_finish_reasons

    fake = types.SimpleNamespace(finish_reasons=["length", "stop"])
    assert extract_finish_reasons(fake) == ["length", "stop"]


def test_extract_finish_reasons_returns_none_when_neither_attribute_present() -> None:
    """Object with neither finish_reasons nor finish_reason → helper returns None."""
    from samantha_server.observability.otel import extract_finish_reasons

    fake = types.SimpleNamespace(text="ok", model_id="m")
    assert extract_finish_reasons(fake) is None


def test_extract_finish_reasons_passes_empty_list_through(  # noqa: E501
) -> None:
    """Plural finish_reasons=[] → helper returns [] (NOT None).

    Empty list is semantically distinct from None: it means the backend
    reported "no reasons" (deliberately empty), versus None which means
    the backend did not report. OTel callers can distinguish the two
    via `is None` vs truthiness; this test pins the contract (PR #323
    review #7).
    """
    from samantha_server.observability.otel import extract_finish_reasons

    fake = types.SimpleNamespace(finish_reasons=[])
    result = extract_finish_reasons(fake)
    assert result == []
    assert result is not None
