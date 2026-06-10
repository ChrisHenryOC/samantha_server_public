"""Span-shape tests: parent ``samantha_server.event`` and ``gen_ai.*`` children.

Asserts the attribute set Phase 3 commits to per
``docs/plans/phase-3-implementation.md § Step 8`` is actually populated
on the spans.

Two test surfaces:

1. End-to-end via the full producer → queue → consumer path. Asserts
   parent span carries ``samantha.*`` attrs sourced from the dispatched
   ``EngineDecision`` and that the child span carries ``gen_ai.*``
   attrs sourced from ``LLMResponse``.
2. The ``gen_ai.response.finish_reasons`` negative case — a production
   ``LLMResponse`` with no field produces a span without the attribute.
"""

from __future__ import annotations

import asyncio
import contextlib
import types
from typing import Any
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from samantha_server.api.events import _QueuePayload
from samantha_server.llm.client import LLMResponse
from samantha_server.queue.priority import EventPriority
from tests.api.helpers import make_clinical_query_ctx_dict, make_consume_app_state


def _drive_one_event(state: Any) -> None:
    """Run a single event through the queue → consumer pipeline."""
    from opentelemetry import context as otel_context
    from opentelemetry import trace

    from samantha_server.api.app import _consume
    from samantha_server.observability.otel import PARENT_SPAN_NAME

    tracer = trace.get_tracer("samantha_server")

    async def run() -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        with tracer.start_as_current_span(PARENT_SPAN_NAME) as span:
            captured_ctx = otel_context.get_current()
            payload = _QueuePayload(
                future=future,
                ctx_dict=make_clinical_query_ctx_dict(order_id="SPAN-001"),
                session_id="span-test",
                priority=EventPriority.ROUTINE,
            )
            await state.queue.put(payload, EventPriority.ROUTINE, otel_context=captured_ctx)

            task = asyncio.create_task(_consume(state))
            try:
                receipt, decision, dispatch_ctx = await asyncio.wait_for(future, timeout=5.0)
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            # Caller stamps span attrs after dispatch resolves —
            # mirrors the events.py post-dispatch path via stamp_trace_attributes.
            # Canonical surface is langfuse.trace.metadata.*; samantha.*
            # is engine-internal only (no metadata duplicate).
            from samantha_server.observability.otel import stamp_trace_attributes
            from samantha_server.observability.trace_context import TraceContext

            stamp_trace_attributes(
                span,
                TraceContext(
                    session_id="span-test",
                    priority="ROUTINE",
                    event_input_hash=decision.event_input_hash,
                    routing_path=dispatch_ctx.routing_path,
                    next_state=decision.next_state,
                    outcome=decision.outcome,
                    latency_us=decision.latency_us,
                    receipt_id=receipt.receipt_id,
                    applied_rule_id=decision.applied_rule_id,
                    environment="production",
                ),
            )

    asyncio.run(run())


def test_gen_ai_child_span_carries_required_attributes(
    otel_exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gen_ai.* child span carries every attribute Phase 3 commits to."""
    # Explicit env isolation — unset means default-on, so gen_ai.prompt
    # IS expected on the span. delenv keeps the test deterministic regardless of
    # any inherited SAMANTHA_STAMP_PROMPT value from the parent process.
    monkeypatch.delenv("SAMANTHA_STAMP_PROMPT", raising=False)
    from samantha_server.observability.otel import LLM_CHILD_SPAN_NAME

    _json_response = LLMResponse(
        text='{"answer_type":"no_orders","order_ids":[],"reasoning":"","caveats":""}',
        input_tokens=42,
        output_tokens=7,
        model_id="test-model-v1",
        latency_us=1000,
    )
    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    mock_llm.complete.return_value = LLMResponse(
        text="ok",
        input_tokens=42,
        output_tokens=7,
        model_id="test-model-v1",
        latency_us=1000,
    )
    mock_llm.complete_json.return_value = _json_response
    state = make_consume_app_state(mock_llm)

    _drive_one_event(state)

    spans = otel_exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None, f"gen_ai child span not found; got {[s.name for s in spans]}"

    attrs = dict(child.attributes or {})
    assert "gen_ai.system" in attrs
    assert attrs["gen_ai.request.model"]
    assert attrs["gen_ai.response.model"] == "test-model-v1"
    assert attrs["gen_ai.usage.input_tokens"] == 42
    assert attrs["gen_ai.usage.output_tokens"] == 7
    assert "gen_ai.request.temperature" in attrs
    assert "gen_ai.request.max_tokens" in attrs
    # Completion is unconditional; prompt is present by default
    # (SAMANTHA_STAMP_PROMPT unset → default-on; disable with explicit falsy value).
    assert "gen_ai.completion" in attrs
    assert "gen_ai.prompt" in attrs


def test_gen_ai_finish_reasons_omitted_when_response_lacks_field(
    otel_exporter: InMemorySpanExporter,
) -> None:
    """Production LLMResponse has no finish_reasons → the attribute is absent."""
    from samantha_server.observability.otel import LLM_CHILD_SPAN_NAME

    _json_resp = LLMResponse(
        text='{"answer_type":"no_orders","order_ids":[],"reasoning":"","caveats":""}',
        input_tokens=1,
        output_tokens=1,
        model_id="m",
        latency_us=1,
    )
    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    mock_llm.complete.return_value = LLMResponse(
        text="ok",
        input_tokens=1,
        output_tokens=1,
        model_id="m",
        latency_us=1,
    )
    mock_llm.complete_json.return_value = _json_resp
    state = make_consume_app_state(mock_llm)

    _drive_one_event(state)

    spans = otel_exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None
    attrs = dict(child.attributes or {})
    # OTel semantic conventions treat absent attributes as "not reported".
    assert "gen_ai.response.finish_reasons" not in attrs


def test_gen_ai_finish_reasons_present_when_response_has_field(
    otel_exporter: InMemorySpanExporter,
) -> None:
    """A duck-typed LLMResponse with finish_reasons populates the attribute verbatim.

    Uses a SimpleNamespace fake — the canonical Step-8 fake-object shape
    documented in phase-3-implementation.md § Step 8.
    """
    from samantha_server.observability.otel import LLM_CHILD_SPAN_NAME

    fake = types.SimpleNamespace(
        text="ok",
        input_tokens=1,
        output_tokens=1,
        model_id="m",
        latency_us=1,
        finish_reasons=["stop"],
    )
    _json_resp = types.SimpleNamespace(
        text='{"answer_type":"no_orders","order_ids":[],"reasoning":"","caveats":""}',
        input_tokens=1,
        output_tokens=1,
        model_id="m",
        latency_us=1,
        finish_reasons=["stop"],  # matches the contract under test
    )
    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    mock_llm.complete.return_value = fake
    mock_llm.complete_json.return_value = _json_resp
    state = make_consume_app_state(mock_llm)

    _drive_one_event(state)

    spans = otel_exporter.get_finished_spans()
    child = next((s for s in spans if s.name == LLM_CHILD_SPAN_NAME), None)
    assert child is not None
    attrs = dict(child.attributes or {})
    assert attrs.get("gen_ai.response.finish_reasons") == ("stop",)


def test_parent_span_carries_required_samantha_attributes(
    otel_exporter: InMemorySpanExporter,
) -> None:
    """Parent samantha_server.event span carries the canonical attribute set.

    routing_path, next_state, outcome, environment are no longer on
    samantha.* (they have langfuse.trace.metadata.* duplicates). The canonical
    surface for those fields is langfuse.trace.metadata.*.
    Engine-internal samantha.* keys (session_id, priority, event_input_hash,
    latency_us, receipt_id) are still emitted.
    """
    from samantha_server.observability.otel import PARENT_SPAN_NAME

    _json_resp = LLMResponse(
        text='{"answer_type":"no_orders","order_ids":[],"reasoning":"","caveats":""}',
        input_tokens=1,
        output_tokens=1,
        model_id="m",
        latency_us=1,
    )
    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    mock_llm.complete.return_value = LLMResponse(
        text="ok",
        input_tokens=1,
        output_tokens=1,
        model_id="m",
        latency_us=1,
    )
    mock_llm.complete_json.return_value = _json_resp
    state = make_consume_app_state(mock_llm)

    _drive_one_event(state)

    spans = otel_exporter.get_finished_spans()
    parent = next((s for s in spans if s.name == PARENT_SPAN_NAME), None)
    assert parent is not None

    attrs = dict(parent.attributes or {})

    # Engine-internal samantha.* keys retained by the schema migration
    assert attrs["samantha.session_id"] == "span-test"
    assert attrs["samantha.priority"] == "ROUTINE"
    assert "samantha.event_input_hash" in attrs
    assert "samantha.latency_us" in attrs
    assert "samantha.receipt_id" in attrs

    # Dropped samantha.* keys must be absent
    assert "samantha.routing_path" not in attrs, "samantha.routing_path must be absent"
    assert "samantha.next_state" not in attrs, "samantha.next_state must be absent"
    assert "samantha.outcome" not in attrs, "samantha.outcome must be absent"
    assert "samantha.environment" not in attrs, "samantha.environment must be absent"

    # Canonical dashboard surface: langfuse.trace.metadata.*
    assert attrs.get("langfuse.trace.metadata.routing_path") == "llm"
    assert "langfuse.trace.metadata.next_state" in attrs
    assert "langfuse.trace.metadata.outcome" in attrs
    assert attrs.get("langfuse.trace.metadata.environment") == "production"
    assert attrs.get("langfuse.environment") == "production"

    # M11: omit-when-None contract — applied_rule_id is None on the
    # clinical_query path, so the attribute must NOT be on the span.
    assert "samantha.applied_rule_id" not in attrs


def test_gen_ai_parse_failure_attribute_set_on_json_parse_failure(
    otel_exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When handle_clinical_query has a JSON parse failure, gen_ai.parse_failure is set on a span.

    Fix #7: parse failures are silent in OTel without this attribute; operators
    watching span attributes can't detect a 100% parse-failure rate. The attribute
    is set by the handler on the current active span (the parent event span) after
    the LLM child span closes and the parse attempt is made.
    """
    from samantha_server import config
    from samantha_server.llm.client import LLMResponse
    from samantha_server.observability.otel import PARENT_SPAN_NAME

    monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    # Return invalid JSON so the handler records parse_failure="json_decode"
    mock_llm.complete.return_value = LLMResponse(
        text="not json at all",
        input_tokens=1,
        output_tokens=1,
        model_id="test-model",
        latency_us=1,
    )
    mock_llm.complete_json.return_value = LLMResponse(
        text="not json at all",
        input_tokens=1,
        output_tokens=1,
        model_id="test-model",
        latency_us=1,
    )
    state = make_consume_app_state(mock_llm)

    _drive_one_event(state)

    spans = otel_exporter.get_finished_spans()
    # The handler sets gen_ai.parse_failure on the current active span (parent) after
    # the child span closes and the parse attempt completes.
    parent = next((s for s in spans if s.name == PARENT_SPAN_NAME), None)
    assert parent is not None, f"parent span not found; got {[s.name for s in spans]}"

    attrs = dict(parent.attributes or {})
    assert "gen_ai.parse_failure" in attrs, (
        f"Expected gen_ai.parse_failure on parent span after JSON parse failure; attrs={attrs}"
    )
    assert attrs["gen_ai.parse_failure"] == "json_decode"
