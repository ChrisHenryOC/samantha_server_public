"""Tests for TraceContext dataclass and stamp_trace_attributes().

Slice 1 of regression-pin the union attribute set before migrating
call sites. Each test corresponds to a specific mapping rule in the union
table defined in the issue.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _make_tracer_with_exporter() -> tuple[object, InMemorySpanExporter]:
    """Return (tracer, exporter) backed by a fresh in-memory provider."""
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    return tracer, exporter


def test_stamp_trace_attributes_full_union_scenario_id_path() -> None:
    """Scenario-id path: all fields except session_id — exact key set.

    Canonical dashboard surface is langfuse.trace.metadata.*. The 7
    dual-stamped samantha.* keys are dropped. Surviving samantha.* keys are
    engine-internal only.

    session_id and scenario_id are mutually exclusive — only
    scenario_id is set here (the sweep/replay scenario-id path).
    run_id wins over sweep_run_id for langfuse.release.
    """
    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()

    ctx = TraceContext(
        scenario_id="SC-001",
        scenario_category="query",
        priority="ROUTINE",
        event_input_hash="a" * 64,
        routing_path="deterministic",
        next_state="ACCESSIONING",
        outcome="acc_001",
        applied_rule_id="ACC-001",
        receipt_id="REC-001",
        environment="replay",
        run_id="run-abc",
        sweep_run_id="sweep-xyz",
        latency_us=500,
    )

    with tracer.start_as_current_span("test") as span:  # type: ignore[union-attr]
        stamp_trace_attributes(span, ctx)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})

    # scenario_id: langfuse fields only (drops samantha.scenario_id)
    assert "samantha.scenario_id" not in attrs, "samantha.scenario_id must not be emitted"
    assert "samantha.session_id" not in attrs, "session_id not set — must not be emitted"
    assert attrs["langfuse.trace.name"] == "SC-001"
    assert attrs["langfuse.trace.metadata.scenario_id"] == "SC-001"
    # scenario_category: langfuse fields only
    assert "samantha.scenario_category" not in attrs, (
        "samantha.scenario_category must not be emitted"
    )
    assert tuple(attrs["langfuse.trace.tags"]) == ("query",)
    assert attrs["langfuse.trace.metadata.scenario_category"] == "query"
    # run_id wins for langfuse.release
    assert attrs["langfuse.release"] == "run-abc"
    # sweep_run_id: langfuse.trace.metadata only
    assert "samantha.sweep_run_id" not in attrs, "samantha.sweep_run_id must not be emitted"
    assert attrs["langfuse.trace.metadata.sweep_run_id"] == "sweep-xyz"
    # environment: langfuse fields only
    assert "samantha.environment" not in attrs, "samantha.environment must not be emitted"
    assert attrs["langfuse.environment"] == "replay"
    assert attrs["langfuse.trace.metadata.environment"] == "replay"
    # priority — engine-internal, kept
    assert attrs["samantha.priority"] == "ROUTINE"
    # event_input_hash — engine-internal, kept
    assert attrs["samantha.event_input_hash"] == "a" * 64
    # routing_path: langfuse.trace.metadata only
    assert "samantha.routing_path" not in attrs
    assert attrs["langfuse.trace.metadata.routing_path"] == "deterministic"
    # next_state: langfuse.trace.metadata only
    assert "samantha.next_state" not in attrs
    assert attrs["langfuse.trace.metadata.next_state"] == "ACCESSIONING"
    # outcome: langfuse.trace.metadata only
    assert "samantha.outcome" not in attrs
    assert attrs["langfuse.trace.metadata.outcome"] == "acc_001"
    # latency_us — engine-internal, kept
    assert attrs["samantha.latency_us"] == 500
    # applied_rule_id — engine-internal, kept
    assert attrs["samantha.applied_rule_id"] == "ACC-001"
    # receipt_id — engine-internal, kept
    assert attrs["samantha.receipt_id"] == "REC-001"

    # Verify the complete key set (no accidental extra attributes)
    expected_keys = {
        # Engine-internal samantha.* (no metadata duplicate)
        "samantha.priority",
        "samantha.event_input_hash",
        "samantha.latency_us",
        "samantha.applied_rule_id",
        "samantha.receipt_id",
        # Langfuse-native field overrides (not part of the dedupe)
        "langfuse.trace.name",
        "langfuse.trace.tags",
        "langfuse.release",
        "langfuse.environment",
        # Canonical dashboard surface: langfuse.trace.metadata.*
        "langfuse.trace.metadata.scenario_id",
        "langfuse.trace.metadata.scenario_category",
        "langfuse.trace.metadata.sweep_run_id",
        "langfuse.trace.metadata.environment",
        "langfuse.trace.metadata.routing_path",
        "langfuse.trace.metadata.next_state",
        "langfuse.trace.metadata.outcome",
    }
    assert set(attrs.keys()) == expected_keys


def test_stamp_trace_attributes_full_union_session_id_path() -> None:
    """Session-id path: all fields except scenario_id — exact key set.

    session_id and scenario_id are mutually exclusive — only
    session_id is set here (the prod / replay dispatch path).
    """
    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()

    ctx = TraceContext(
        session_id="sess-001",
        priority="ROUTINE",
        event_input_hash="a" * 64,
        routing_path="deterministic",
        next_state="ACCESSIONING",
        outcome="acc_001",
        applied_rule_id="ACC-001",
        receipt_id="REC-001",
        environment="replay",
        latency_us=500,
    )

    with tracer.start_as_current_span("test") as span:  # type: ignore[union-attr]
        stamp_trace_attributes(span, ctx)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})

    # session_id emits samantha.session_id + langfuse.trace.name + metadata.scenario_id
    assert attrs["samantha.session_id"] == "sess-001"
    assert attrs["langfuse.trace.name"] == "sess-001"
    assert attrs["langfuse.trace.metadata.scenario_id"] == "sess-001"

    # Engine-internal keys present
    assert attrs["samantha.priority"] == "ROUTINE"
    assert attrs["samantha.event_input_hash"] == "a" * 64
    assert attrs["samantha.latency_us"] == 500
    assert attrs["samantha.applied_rule_id"] == "ACC-001"
    assert attrs["samantha.receipt_id"] == "REC-001"

    # Dropped samantha.* keys absent
    assert "samantha.routing_path" not in attrs
    assert "samantha.next_state" not in attrs
    assert "samantha.outcome" not in attrs
    assert "samantha.environment" not in attrs

    # Canonical surface
    assert attrs["langfuse.trace.metadata.routing_path"] == "deterministic"
    assert attrs["langfuse.trace.metadata.next_state"] == "ACCESSIONING"
    assert attrs["langfuse.trace.metadata.outcome"] == "acc_001"
    assert attrs["langfuse.environment"] == "replay"
    assert attrs["langfuse.trace.metadata.environment"] == "replay"

    # Exact key-set pin (regression canary). Mirrors the scenario-id-path test.
    expected_keys = {
        "samantha.session_id",
        "samantha.priority",
        "samantha.event_input_hash",
        "samantha.latency_us",
        "samantha.applied_rule_id",
        "samantha.receipt_id",
        "langfuse.trace.name",
        "langfuse.environment",
        "langfuse.trace.metadata.scenario_id",
        "langfuse.trace.metadata.environment",
        "langfuse.trace.metadata.routing_path",
        "langfuse.trace.metadata.next_state",
        "langfuse.trace.metadata.outcome",
    }
    assert set(attrs.keys()) == expected_keys, (
        f"Attribute key-set drift on session-id path: "
        f"extra={set(attrs.keys()) - expected_keys!r}, "
        f"missing={expected_keys - set(attrs.keys())!r}"
    )


def test_stamp_trace_attributes_scenario_id_populates_langfuse_name() -> None:
    """When scenario_id is set, it populates langfuse.trace.name and
    langfuse.trace.metadata.scenario_id.

    session_id and scenario_id are mutually exclusive.
    This test verifies the scenario_id path in isolation.
    samantha.scenario_id is dropped; canonical surface is
    langfuse.trace.metadata.scenario_id.
    """
    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()

    ctx = TraceContext(scenario_id="SC-WINNER")

    with tracer.start_as_current_span("test") as span:  # type: ignore[union-attr]
        stamp_trace_attributes(span, ctx)

    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    # scenario_id wins for langfuse.trace.name and langfuse.trace.metadata.scenario_id
    assert "samantha.scenario_id" not in attrs, "samantha.scenario_id must be absent"
    assert "samantha.session_id" not in attrs, "session_id not set — must be absent"
    assert attrs["langfuse.trace.name"] == "SC-WINNER"
    assert attrs["langfuse.trace.metadata.scenario_id"] == "SC-WINNER"


def test_stamp_trace_attributes_session_id_fallback_for_langfuse_name() -> None:
    """When scenario_id is None and session_id is set, session_id populates
    langfuse.trace.name and langfuse.trace.metadata.scenario_id.
    """
    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()

    ctx = TraceContext(
        session_id="sess-fallback",
        scenario_id=None,
    )

    with tracer.start_as_current_span("test") as span:  # type: ignore[union-attr]
        stamp_trace_attributes(span, ctx)

    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attrs["samantha.session_id"] == "sess-fallback"
    assert "samantha.scenario_id" not in attrs
    assert attrs["langfuse.trace.name"] == "sess-fallback"
    assert attrs["langfuse.trace.metadata.scenario_id"] == "sess-fallback"


def test_stamp_trace_attributes_run_id_wins_over_sweep_run_id_for_release() -> None:
    """When both run_id and sweep_run_id are set, run_id wins for langfuse.release."""
    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()

    ctx = TraceContext(
        run_id="run-winner",
        sweep_run_id="sweep-loser",
    )

    with tracer.start_as_current_span("test") as span:  # type: ignore[union-attr]
        stamp_trace_attributes(span, ctx)

    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attrs["langfuse.release"] == "run-winner"
    # samantha.sweep_run_id dropped; canonical surface is metadata.*
    assert "samantha.sweep_run_id" not in attrs
    assert attrs["langfuse.trace.metadata.sweep_run_id"] == "sweep-loser"


def test_stamp_trace_attributes_sweep_run_id_fallback_for_release() -> None:
    """When run_id is None, sweep_run_id populates langfuse.release."""
    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()

    ctx = TraceContext(
        sweep_run_id="sweep-release",
        run_id=None,
    )

    with tracer.start_as_current_span("test") as span:  # type: ignore[union-attr]
        stamp_trace_attributes(span, ctx)

    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attrs["langfuse.release"] == "sweep-release"


def test_stamp_trace_attributes_all_none_emits_nothing() -> None:
    """An all-None TraceContext stamps no attributes."""
    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()

    ctx = TraceContext()

    with tracer.start_as_current_span("test") as span:  # type: ignore[union-attr]
        stamp_trace_attributes(span, ctx)

    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attrs == {}


def test_stamp_trace_attributes_fail_soft_on_allowlist_rejection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """stamp_trace_attributes swallows ValueError from set_span_attribute and
    logs a warning with the 'stamp_trace_attributes: allowlist rejection' prefix.
    Must not raise.
    """
    from opentelemetry.sdk.trace import TracerProvider

    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    provider = TracerProvider()
    tracer = provider.get_tracer("test")

    ctx = TraceContext(session_id="s", priority="ROUTINE")

    with (
        caplog.at_level(logging.WARNING, logger="samantha_server.observability.otel"),
        tracer.start_as_current_span("test") as span,
        patch(
            "samantha_server.observability.otel.set_span_attribute",
            side_effect=ValueError("forced allowlist rejection"),
        ),
    ):
        # Must NOT raise — fail-soft posture.
        stamp_trace_attributes(span, ctx)

    assert any(
        "stamp_trace_attributes: allowlist rejection" in r.message for r in caplog.records
    ), f"Expected warning log; got: {[r.message for r in caplog.records]}"


def test_stamp_trace_attributes_fail_soft_on_sweep_pattern(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fail-soft on the sweep-style call pattern.

    Replaces the deleted ``test_stamp_sweep_span_attributes_swallows_allowlist_rejection``.
    The sweep-style context populates ``scenario_id`` / ``sweep_run_id`` rather
    than ``session_id``, which exercises a different early branch of
    ``stamp_trace_attributes``. A future change that moves some branches outside
    the function-boundary try/except would silently lose the fail-soft guarantee
    on this surface without the dedicated case.
    """
    from opentelemetry.sdk.trace import TracerProvider

    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    provider = TracerProvider()
    tracer = provider.get_tracer("test")

    ctx = TraceContext(
        scenario_id="SC-100",
        scenario_category="query",
        sweep_run_id="run-abc",
    )

    with (
        caplog.at_level(logging.WARNING, logger="samantha_server.observability.otel"),
        tracer.start_as_current_span("test") as span,
        patch(
            "samantha_server.observability.otel.set_span_attribute",
            side_effect=ValueError("forced allowlist rejection"),
        ),
    ):
        stamp_trace_attributes(span, ctx)

    assert any(
        "stamp_trace_attributes: allowlist rejection" in r.message for r in caplog.records
    ), f"Expected warning log; got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# TraceContext.order_id + samantha.order_id span attribute
# ---------------------------------------------------------------------------


def test_trace_context_order_id_defaults_to_none() -> None:
    """TraceContext.order_id has an additive optional default of None."""
    from samantha_server.observability.trace_context import TraceContext

    ctx = TraceContext()
    assert ctx.order_id is None


def test_stamp_trace_attributes_emits_order_id_when_set() -> None:
    """When TraceContext.order_id is set, stamp_trace_attributes emits samantha.order_id."""
    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()
    ctx = TraceContext(order_id="O-TRACE-001")

    with tracer.start_as_current_span("test") as span:  # type: ignore[union-attr]
        stamp_trace_attributes(span, ctx)

    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attrs["samantha.order_id"] == "O-TRACE-001"


def test_stamp_trace_attributes_omits_order_id_when_none() -> None:
    """When TraceContext.order_id is None, samantha.order_id is not emitted."""
    from samantha_server.observability.otel import stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()
    ctx = TraceContext(session_id="sess-xyz", order_id=None)

    with tracer.start_as_current_span("test") as span:  # type: ignore[union-attr]
        stamp_trace_attributes(span, ctx)

    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert "samantha.order_id" not in attrs
