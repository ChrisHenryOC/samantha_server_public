"""Trace-context propagation across the queue boundary.

Per phase-3-implementation.md § Step 8: the orchestrator's queue
consumer task runs in a different asyncio task than the request
handler, so the parent ``samantha_server.event`` span context must be
propagated explicitly across enqueue. The test exercises the full
producer → queue → consumer path and asserts that LLM-call child spans
share their ``trace_id`` with the parent span.

Without this propagation, child spans would become orphan traces and
the dashboard's per-decision view would silently fragment.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from samantha_server.api.events import _QueuePayload
from samantha_server.engine.decision import EngineDecision
from samantha_server.queue.priority import EventPriority
from samantha_server.receipts.signing import SignedReceipt
from tests.api.helpers import make_clinical_query_ctx_dict, make_consume_app_state


def test_dispatch_span_nests_under_sweep_span(otel_exporter: InMemorySpanExporter) -> None:
    """PR #164 review M-3: when a live_llm test wraps dispatch_event in
    ``sweep_span_context``, the orchestrator's ``samantha_server.event``
    parent span must nest as a child of the outer
    ``samantha_server.scenario_sweep`` span. A future context-propagation
    regression that broke this would silently produce two root spans per
    fixture in Langfuse, with CI staying green (live_llm tests skip
    without credentials)."""
    from tests.scenarios.conftest import sweep_span_context

    tracer = trace.get_tracer("samantha_server.tests")

    # Simulate the production pattern: outer sweep span; inner dispatch span
    # opened with `start_as_current_span` (mirrors what events.py does when
    # it opens the PARENT_SPAN_NAME span for the production path).
    with (
        sweep_span_context(
            scenario_id="SC-T1",
            scenario_category="query",
            sweep_run_id="run-test",
        ),
        tracer.start_as_current_span("samantha_server.event"),
    ):
        pass  # the span itself is what we measure

    spans = {s.name: s for s in otel_exporter.get_finished_spans()}
    sweep = spans["samantha_server.scenario_sweep"]
    dispatch = spans["samantha_server.event"]

    assert dispatch.parent is not None, "dispatch span has no parent — nesting broken"
    assert dispatch.parent.span_id == sweep.context.span_id, (
        f"dispatch span's parent_id {dispatch.parent.span_id!r} does not match "
        f"sweep span's span_id {sweep.context.span_id!r}; nesting invariant broken"
    )
    assert dispatch.context.trace_id == sweep.context.trace_id, (
        "dispatch span and sweep span must share trace_id"
    )


def test_llm_child_span_shares_trace_id_with_parent_across_queue(
    otel_exporter: InMemorySpanExporter,
) -> None:
    """Parent samantha_server.event span and gen_ai child span share trace_id."""
    from samantha_server.api.app import _consume
    from samantha_server.observability.otel import PARENT_SPAN_NAME

    state = make_consume_app_state()
    tracer = trace.get_tracer("samantha_server")

    async def run() -> None:
        future: asyncio.Future[tuple[SignedReceipt, EngineDecision, Any]] = (
            asyncio.get_running_loop().create_future()
        )

        # Producer: capture the parent context inside the parent span and
        # enqueue with otel_context — this mirrors the events.py path.
        with tracer.start_as_current_span(PARENT_SPAN_NAME):
            from opentelemetry import context as otel_context

            captured_ctx = otel_context.get_current()

            payload = _QueuePayload(
                future=future,
                ctx_dict=make_clinical_query_ctx_dict(order_id="TRACE-001"),
                session_id="trace-test",
                priority=EventPriority.ROUTINE,
            )

            await state.queue.put(
                payload,
                EventPriority.ROUTINE,
                otel_context=captured_ctx,
            )

            task = asyncio.create_task(_consume(state))
            try:
                await asyncio.wait_for(future, timeout=5.0)
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    asyncio.run(run())

    spans = otel_exporter.get_finished_spans()
    parent = next((s for s in spans if s.name == PARENT_SPAN_NAME), None)
    children = [s for s in spans if s.name != PARENT_SPAN_NAME]

    assert parent is not None, f"parent span not found; got names={[s.name for s in spans]}"
    assert children, f"no child spans found; got names={[s.name for s in spans]}"

    parent_trace_id = parent.context.trace_id
    for child in children:
        assert child.context.trace_id == parent_trace_id, (
            f"child span {child.name!r} trace_id={child.context.trace_id} "
            f"differs from parent trace_id={parent_trace_id} — queue boundary "
            "propagation is broken"
        )
