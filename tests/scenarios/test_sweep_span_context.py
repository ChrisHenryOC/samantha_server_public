"""Regression test for sweep_span_context attribute set after GH-182 migration.

Slice 4: pin the exact attribute set emitted by sweep_span_context after
migration from stamp_sweep_span_attributes to stamp_trace_attributes(TraceContext(...)).
The attribute set must be identical to the legacy set defined in
test_otel_phi.py::test_stamp_sweep_span_attributes_sets_g5_tags.
"""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_sweep_span_context_emits_same_attribute_set_as_legacy_stamper() -> None:
    """sweep_span_context emits the same full attribute set as
    stamp_sweep_span_attributes (pinned in test_stamp_sweep_span_attributes_sets_g5_tags).

    This regression test ensures the migration from stamp_sweep_span_attributes
    to stamp_trace_attributes(TraceContext(...)) produces identical spans.

    Uses a self-contained TracerProvider (not the global one) by calling
    sweep_span_context's inner logic directly on a local span.
    """

    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    # Mirror what sweep_span_context does internally — open a span, stamp it.
    with tracer.start_as_current_span("samantha_server.scenario_sweep") as sweep_span:
        stamp_trace_attributes(
            sweep_span,
            TraceContext(
                scenario_id="SC-100",
                scenario_category="unknown_input",
                sweep_run_id="run-abc",
            ),
        )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})

    # GH-183: canonical dashboard surface = langfuse.trace.metadata.*
    # samantha.scenario_id, samantha.scenario_category, samantha.sweep_run_id
    # are dropped (they have langfuse.trace.metadata.* duplicates).
    expected_keys = {
        "langfuse.trace.name",
        "langfuse.trace.tags",
        "langfuse.release",
        "langfuse.trace.metadata.scenario_id",
        "langfuse.trace.metadata.scenario_category",
        "langfuse.trace.metadata.sweep_run_id",
    }
    assert set(attrs.keys()) == expected_keys, (
        f"Attribute set mismatch. Extra: {set(attrs.keys()) - expected_keys!r}, "
        f"Missing: {expected_keys - set(attrs.keys())!r}"
    )

    # Dropped samantha.* keys must be absent
    assert "samantha.scenario_id" not in attrs, "GH-183: samantha.scenario_id must be absent"
    assert "samantha.scenario_category" not in attrs, (
        "GH-183: samantha.scenario_category must be absent"
    )
    assert "samantha.sweep_run_id" not in attrs, "GH-183: samantha.sweep_run_id must be absent"

    # Canonical surface assertions
    assert attrs["langfuse.trace.name"] == "SC-100"
    assert tuple(attrs["langfuse.trace.tags"]) == ("unknown_input",)
    assert attrs["langfuse.release"] == "run-abc"
    assert attrs["langfuse.trace.metadata.scenario_id"] == "SC-100"
    assert attrs["langfuse.trace.metadata.scenario_category"] == "unknown_input"
    assert attrs["langfuse.trace.metadata.sweep_run_id"] == "run-abc"
