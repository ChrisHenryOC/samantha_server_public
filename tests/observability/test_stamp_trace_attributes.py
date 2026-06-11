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


# ---------------------------------------------------------------------------
# Per-attribute exception containment
# ---------------------------------------------------------------------------


def test_stamp_trace_attributes_mid_batch_rejection_does_not_drop_later_attrs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A single mid-batch rejection must NOT drop attributes stamped after it.

    The old function-boundary try/except silently dropped every
    attribute after the first rejection. This test is the acceptance criterion:
    attributes before AND after the rejected key must all appear on the span.

    Stamping order (condensed for this test):
      1. samantha.session_id          <- before rejection
      2. langfuse.trace.name          <- before rejection
      3. langfuse.trace.metadata.scenario_id  <- before rejection
      4. langfuse.environment         <- before rejection
      5. langfuse.trace.metadata.environment  <- before rejection
      6. samantha.priority            <- REJECTED (monkeypatched)
      7. langfuse.trace.metadata.outcome  <- after rejection (must survive)
      8. samantha.receipt_id          <- after rejection (must survive)
    """
    import samantha_server.observability.otel as otel_module
    from samantha_server.observability.otel import set_span_attribute, stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()

    ctx = TraceContext(
        session_id="sess-381",
        environment="replay",
        priority="ROUTINE",
        outcome="acc_001",
        receipt_id="REC-381",
    )

    _real = set_span_attribute

    def _reject_priority(span: object, name: str, value: object) -> None:
        if name == "samantha.priority":
            raise ValueError(f"OTel span attribute {name!r} is not on the allowlist.")
        _real(span, name, value)  # type: ignore[arg-type]

    with (
        caplog.at_level(logging.WARNING, logger="samantha_server.observability.otel"),
        tracer.start_as_current_span("test") as span,
        patch.object(otel_module, "set_span_attribute", side_effect=_reject_priority),
    ):
        stamp_trace_attributes(span, ctx)

    attrs = dict(exporter.get_finished_spans()[0].attributes or {})

    # (a) Attributes stamped BEFORE the rejected key are present.
    assert attrs.get("samantha.session_id") == "sess-381"
    assert attrs.get("langfuse.trace.name") == "sess-381"
    assert attrs.get("langfuse.trace.metadata.scenario_id") == "sess-381"
    assert attrs.get("langfuse.environment") == "replay"
    assert attrs.get("langfuse.trace.metadata.environment") == "replay"

    # (b) Attributes stamped AFTER the rejected key are ALSO present.
    assert attrs.get("langfuse.trace.metadata.outcome") == "acc_001", (
        "Outcome must not be silently dropped by a prior rejection"
    )
    assert attrs.get("samantha.receipt_id") == "REC-381", (
        "receipt_id must not be silently dropped by a prior rejection"
    )

    # (c) The rejected attribute itself is absent.
    assert "samantha.priority" not in attrs

    # (d) A warning was logged naming the rejected attribute.
    assert any(
        "stamp_trace_attributes: allowlist rejection" in r.message
        and "samantha.priority" in r.message
        for r in caplog.records
    ), f"Expected warning naming 'samantha.priority'; got: {[r.message for r in caplog.records]}"

    # (e) The warning carries the accurate containment statement, not the old
    # claim that subsequent attributes were dropped (pin the
    # affirmative tail so a neutral rewording can't silently weaken it).
    for record in caplog.records:
        if "stamp_trace_attributes: allowlist rejection" in record.message:
            assert "Subsequent attributes" not in record.message, (
                "Warning must not falsely claim subsequent attrs were not emitted"
            )
            assert "other attributes in this call are unaffected" in record.message


def test_stamp_trace_attributes_multiple_rejections_stamps_everything_else(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two rejections each produce a warning; all other attributes are stamped.

    Slice 2 verifies the per-attribute loop handles multiple bad keys
    in a single call without short-circuiting on the first failure.

    Rejected keys: samantha.priority (middle) and samantha.receipt_id (late).
    All other keys set in the context must appear on the span.
    """
    import samantha_server.observability.otel as otel_module
    from samantha_server.observability.otel import set_span_attribute, stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()

    ctx = TraceContext(
        session_id="sess-381b",
        environment="replay",
        priority="ROUTINE",
        outcome="acc_002",
        receipt_id="REC-381b",
    )

    _real = set_span_attribute
    _rejected = {"samantha.priority", "samantha.receipt_id"}

    def _reject_two(span: object, name: str, value: object) -> None:
        if name in _rejected:
            raise ValueError(f"OTel span attribute {name!r} is not on the allowlist.")
        _real(span, name, value)  # type: ignore[arg-type]

    with (
        caplog.at_level(logging.WARNING, logger="samantha_server.observability.otel"),
        tracer.start_as_current_span("test") as span,
        patch.object(otel_module, "set_span_attribute", side_effect=_reject_two),
    ):
        stamp_trace_attributes(span, ctx)

    attrs = dict(exporter.get_finished_spans()[0].attributes or {})

    # Both rejected keys are absent.
    assert "samantha.priority" not in attrs
    assert "samantha.receipt_id" not in attrs

    # All other expected keys are stamped.
    assert attrs.get("samantha.session_id") == "sess-381b"
    assert attrs.get("langfuse.trace.name") == "sess-381b"
    assert attrs.get("langfuse.trace.metadata.scenario_id") == "sess-381b"
    assert attrs.get("langfuse.environment") == "replay"
    assert attrs.get("langfuse.trace.metadata.environment") == "replay"
    assert attrs.get("langfuse.trace.metadata.outcome") == "acc_002"

    # Exactly two warnings, one per rejected attribute.
    rejection_records = [
        r for r in caplog.records if "stamp_trace_attributes: allowlist rejection" in r.message
    ]
    assert len(rejection_records) == 2, (
        f"Expected 2 rejection warnings, got {len(rejection_records)}: "
        f"{[r.message for r in rejection_records]}"
    )
    warning_text = " ".join(r.message for r in rejection_records)
    assert "samantha.priority" in warning_text
    assert "samantha.receipt_id" in warning_text


@pytest.mark.parametrize(
    ("ctx_kwargs", "rejected_key", "surviving", "case"),
    [
        pytest.param(
            {"session_id": "sess-pos", "priority": "ROUTINE", "order_id": "O-POS-1"},
            "samantha.session_id",
            {
                "langfuse.trace.name": "sess-pos",
                "langfuse.trace.metadata.scenario_id": "sess-pos",
                "samantha.priority": "ROUTINE",
                "samantha.order_id": "O-POS-1",
            },
            "first stamped key",
            id="first-key",
        ),
        pytest.param(
            {"session_id": "sess-pos", "priority": "ROUTINE", "order_id": "O-POS-2"},
            "samantha.order_id",
            {
                "samantha.session_id": "sess-pos",
                "langfuse.trace.name": "sess-pos",
                "samantha.priority": "ROUTINE",
            },
            "last stamped key",
            id="last-key",
        ),
        pytest.param(
            {"scenario_category": "query", "priority": "ROUTINE"},
            "langfuse.trace.tags",
            {
                "langfuse.trace.metadata.scenario_category": "query",
                "samantha.priority": "ROUTINE",
            },
            "list-valued key",
            id="list-valued-key",
        ),
        pytest.param(
            {"scenario_id": "SC-POS", "order_id": "O-POS-3"},
            "langfuse.trace.name",
            {
                "langfuse.trace.metadata.scenario_id": "SC-POS",
                "samantha.order_id": "O-POS-3",
            },
            "scenario_id-branch key",
            id="scenario-branch-key",
        ),
    ],
)
def test_stamp_trace_attributes_rejection_position_containment(
    caplog: pytest.LogCaptureFixture,
    ctx_kwargs: dict[str, str],
    rejected_key: str,
    surviving: dict[str, str],
    case: str,
) -> None:
    """Containment holds regardless of WHERE the rejection lands.

    The slice-1/slice-2 tests only ever reject a mid-batch, session-branch,
    scalar key. This parametrization pins the boundary positions (first and
    last stamped key), the single list-valued attribute, and the
    scenario_id-branch keys: in every case the rejected key is absent, every
    other key survives, and one warning names the rejected key.
    """
    import samantha_server.observability.otel as otel_module
    from samantha_server.observability.otel import set_span_attribute, stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()
    ctx = TraceContext(**ctx_kwargs)  # type: ignore[arg-type]

    _real = set_span_attribute

    def _reject_one(span: object, name: str, value: object) -> None:
        if name == rejected_key:
            raise ValueError(f"OTel span attribute {name!r} is not on the allowlist.")
        _real(span, name, value)  # type: ignore[arg-type]

    with (
        caplog.at_level(logging.WARNING, logger="samantha_server.observability.otel"),
        tracer.start_as_current_span("test") as span,
        patch.object(otel_module, "set_span_attribute", side_effect=_reject_one),
    ):
        stamp_trace_attributes(span, ctx)

    attrs = dict(exporter.get_finished_spans()[0].attributes or {})

    assert rejected_key not in attrs, f"{case}: rejected key must be absent"
    for key, value in surviving.items():
        got = attrs.get(key)
        if key == "langfuse.trace.tags":
            got = tuple(got or ())
        assert got == value, f"{case}: {key!r} must survive the rejection of {rejected_key!r}"

    rejection_records = [
        r for r in caplog.records if "stamp_trace_attributes: allowlist rejection" in r.message
    ]
    assert len(rejection_records) == 1, f"{case}: exactly one warning expected"
    assert rejected_key in rejection_records[0].message


def test_stamp_trace_attributes_non_valueerror_is_contained(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-ValueError from the OTel SDK must not escape stamp_trace_attributes.

    the fail-soft contract says observability must not gate
    the decision return, but only ValueError was contained — a TypeError from
    the SDK (e.g. BoundedAttributes' immutable-span guard) would propagate
    into the request handler as a 500 with a dropped trace. The warning is
    key + exception type only (G18: never echo the value, which can be PHI).
    """
    import samantha_server.observability.otel as otel_module
    from samantha_server.observability.otel import set_span_attribute, stamp_trace_attributes
    from samantha_server.observability.trace_context import TraceContext

    tracer, exporter = _make_tracer_with_exporter()

    ctx = TraceContext(session_id="sess-te", priority="ROUTINE", receipt_id="REC-TE")

    _real = set_span_attribute

    def _typeerror_on_priority(span: object, name: str, value: object) -> None:
        if name == "samantha.priority":
            raise TypeError("immutable span attributes")
        _real(span, name, value)  # type: ignore[arg-type]

    with (
        caplog.at_level(logging.WARNING, logger="samantha_server.observability.otel"),
        tracer.start_as_current_span("test") as span,
        patch.object(otel_module, "set_span_attribute", side_effect=_typeerror_on_priority),
    ):
        # Must NOT raise — fail-soft posture covers more than ValueError.
        stamp_trace_attributes(span, ctx)

    attrs = dict(exporter.get_finished_spans()[0].attributes or {})
    assert attrs.get("samantha.session_id") == "sess-te"
    assert attrs.get("samantha.receipt_id") == "REC-TE", (
        "attributes after the failing key must survive"
    )
    assert "samantha.priority" not in attrs

    failure_records = [
        r for r in caplog.records if "samantha.priority" in r.message and "TypeError" in r.message
    ]
    assert failure_records, (
        f"Expected a warning naming the key and exception type; "
        f"got: {[r.message for r in caplog.records]}"
    )
