"""Tests for GH-321 S2.5: gen_ai.response.finish_reason_is_length boolean span attribute.

When extract_finish_reasons(response) returns a list containing "length",
both llm_complete_with_span and llm_complete_json_with_span must set
gen_ai.response.finish_reason_is_length=True on the LLM child span.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from samantha_server.llm.client import LLMResponse


def _make_provider_and_exporter() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _patch_get_tracer(monkeypatch: pytest.MonkeyPatch, provider: TracerProvider) -> None:
    from opentelemetry import trace as otel_trace

    monkeypatch.setattr(otel_trace, "get_tracer", lambda name: provider.get_tracer(name))


def _make_response_with_finish(finish_reason: str | None) -> LLMResponse:
    return LLMResponse(
        text="ok",
        input_tokens=10,
        output_tokens=5,
        model_id="test-model-v1",
        latency_us=1000,
        finish_reason=finish_reason,
    )


def _make_mock_llm(finish_reason: str | None) -> Any:
    mock = MagicMock()
    mock.model_id = "test-model"
    response = _make_response_with_finish(finish_reason)
    mock.complete.return_value = response
    mock.complete_json.return_value = response
    return mock


# ---------------------------------------------------------------------------
# Allowlist membership
# ---------------------------------------------------------------------------


def test_finish_reason_is_length_attr_is_on_allowlist() -> None:
    """GH-321: gen_ai.response.finish_reason_is_length must be in _ALLOWED_ATTRIBUTES."""
    from samantha_server.observability.otel import _ALLOWED_ATTRIBUTES

    assert "gen_ai.response.finish_reason_is_length" in _ALLOWED_ATTRIBUTES


# ---------------------------------------------------------------------------
# llm_complete_with_span
# ---------------------------------------------------------------------------


def test_complete_with_span_sets_finish_reason_is_length_true_when_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llm_complete_with_span sets gen_ai.response.finish_reason_is_length=True
    when finish_reason='length'."""
    from samantha_server.observability.otel import LLM_CHILD_SPAN_NAME, llm_complete_with_span

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)
    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)

    mock_llm = _make_mock_llm("length")
    llm_complete_with_span(mock_llm, "test prompt")

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None
    attrs = dict(child.attributes or {})
    assert attrs.get("gen_ai.response.finish_reason_is_length") is True


def test_complete_with_span_does_not_set_finish_reason_is_length_when_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llm_complete_with_span does NOT set gen_ai.response.finish_reason_is_length
    when finish_reason='stop'."""
    from samantha_server.observability.otel import LLM_CHILD_SPAN_NAME, llm_complete_with_span

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)
    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)

    mock_llm = _make_mock_llm("stop")
    llm_complete_with_span(mock_llm, "test prompt")

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None
    attrs = dict(child.attributes or {})
    assert "gen_ai.response.finish_reason_is_length" not in attrs


def test_complete_with_span_does_not_set_finish_reason_is_length_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llm_complete_with_span does NOT set gen_ai.response.finish_reason_is_length
    when finish_reason=None."""
    from samantha_server.observability.otel import LLM_CHILD_SPAN_NAME, llm_complete_with_span

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)
    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)

    mock_llm = _make_mock_llm(None)
    llm_complete_with_span(mock_llm, "test prompt")

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None
    attrs = dict(child.attributes or {})
    assert "gen_ai.response.finish_reason_is_length" not in attrs


# ---------------------------------------------------------------------------
# llm_complete_json_with_span
# ---------------------------------------------------------------------------

_TEST_SCHEMA = {
    "type": "object",
    "properties": {"answer_type": {"type": "string"}},
    "additionalProperties": False,
    "required": ["answer_type"],
}
_TEST_MESSAGES = [{"role": "user", "content": "Which orders are ready?"}]


def test_complete_json_with_span_sets_finish_reason_is_length_true_when_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llm_complete_json_with_span sets gen_ai.response.finish_reason_is_length=True
    when finish_reason='length'."""
    from samantha_server.observability.otel import LLM_CHILD_SPAN_NAME, llm_complete_json_with_span

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)
    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)

    mock_llm = _make_mock_llm("length")
    llm_complete_json_with_span(mock_llm, _TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="t")

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None
    attrs = dict(child.attributes or {})
    assert attrs.get("gen_ai.response.finish_reason_is_length") is True


def test_complete_json_with_span_does_not_set_finish_reason_is_length_when_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llm_complete_json_with_span does NOT set gen_ai.response.finish_reason_is_length
    when finish_reason='stop'."""
    from samantha_server.observability.otel import LLM_CHILD_SPAN_NAME, llm_complete_json_with_span

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)
    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)

    mock_llm = _make_mock_llm("stop")
    llm_complete_json_with_span(mock_llm, _TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="t")

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None
    attrs = dict(child.attributes or {})
    assert "gen_ai.response.finish_reason_is_length" not in attrs


def test_complete_json_with_span_does_not_set_finish_reason_is_length_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llm_complete_json_with_span does NOT set gen_ai.response.finish_reason_is_length
    when finish_reason=None."""
    from samantha_server.observability.otel import LLM_CHILD_SPAN_NAME, llm_complete_json_with_span

    provider, exporter = _make_provider_and_exporter()
    _patch_get_tracer(monkeypatch, provider)
    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)

    mock_llm = _make_mock_llm(None)
    llm_complete_json_with_span(mock_llm, _TEST_MESSAGES, schema=_TEST_SCHEMA, schema_name="t")

    spans = exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None
    attrs = dict(child.attributes or {})
    assert "gen_ai.response.finish_reason_is_length" not in attrs
