"""Architectural tests for the OTel attribute allowlist.

Two-part guard for the PHI boundary on the new span exit:

1. Deterministic positive case — every name in the canonical allowlist
   is accepted by ``set_span_attribute``. Catches an inverted-predicate
   bug that the property test alone would miss (Hypothesis would happily
   keep generating non-allowlisted names that pass an inverted assertion).
2. Property negative case — randomly-generated non-allowlisted names
   raise on ``set_span_attribute``.

``gen_ai.completion`` is unconditionally allowlisted (lab-host
topology). ``gen_ai.prompt`` is allowlisted and stamped by default;
suppressed only by explicit falsy ``SAMANTHA_STAMP_PROMPT``. The old G8 ban
tests have been updated to reflect this policy change.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def test_allowlist_has_every_documented_name() -> None:
    """Every attribute in the canonical schema is on the allowlist.

    samantha.* is engine-internal only (no metadata duplicates).
    Dropped samantha.* keys (samantha.routing_path, samantha.next_state,
    samantha.outcome, samantha.scenario_id, samantha.scenario_category,
    samantha.sweep_run_id, samantha.environment) must NOT be on the allowlist —
    they have langfuse.trace.metadata.* duplicates and are no longer emitted.
    """
    from samantha_server.observability.otel import _ALLOWED_ATTRIBUTES

    documented = {
        # samantha.* engine-internal parent-span attributes
        "samantha.event_input_hash",
        "samantha.session_id",
        "samantha.priority",
        "samantha.applied_rule_id",
        "samantha.latency_us",
        "samantha.receipt_id",
        # gen_ai.* child-span attributes
        "gen_ai.system",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.request.temperature",
        "gen_ai.request.max_tokens",
        "gen_ai.response.finish_reasons",
        "gen_ai.completion",
        "gen_ai.prompt",
    }
    assert documented <= _ALLOWED_ATTRIBUTES

    # Dropped samantha.* keys must NOT be on the allowlist
    dropped = {
        "samantha.routing_path",
        "samantha.next_state",
        "samantha.outcome",
        "samantha.scenario_id",
        "samantha.scenario_category",
        "samantha.sweep_run_id",
        "samantha.environment",
    }
    present_dropped = dropped & _ALLOWED_ATTRIBUTES
    assert not present_dropped, (
        f"These samantha.* keys must be removed from the allowlist "
        f"(they have langfuse.trace.metadata.* duplicates): {present_dropped!r}"
    )


def test_environment_parity_constant_exposed() -> None:
    """ENVIRONMENT_PARITY constant exists alongside ENVIRONMENT_REPLAY
    and ENVIRONMENT_PRODUCTION; value is `"parity"`."""
    from samantha_server.observability import otel

    assert hasattr(otel, "ENVIRONMENT_PARITY"), (
        "ENVIRONMENT_PARITY missing — parity-replay traces need a "
        "discriminator value alongside ENVIRONMENT_PRODUCTION / "
        "ENVIRONMENT_REPLAY."
    )
    assert otel.ENVIRONMENT_PARITY == "parity"
    assert "ENVIRONMENT_PARITY" in otel.__all__


def test_g5_sweep_attributes_use_canonical_langfuse_surface() -> None:
    """Sweep attributes surface via langfuse.trace.metadata.*, not samantha.*.

     originally allowlisted samantha.scenario_id, samantha.scenario_category
    samantha.sweep_run_id. drops those keys (they have metadata duplicates);
    the canonical surface is now langfuse.trace.metadata.*.

    This test ensures:
    1. The canonical langfuse.trace.metadata.* keys are on the allowlist.
    2. The dropped samantha.* keys are NOT on the allowlist (PHI-boundary is
       a one-way schema change: removing from allowlist prevents future stamping).
    """
    from samantha_server.observability.otel import _ALLOWED_ATTRIBUTES

    canonical = {
        "langfuse.trace.metadata.scenario_id",
        "langfuse.trace.metadata.scenario_category",
        "langfuse.trace.metadata.sweep_run_id",
    }
    assert canonical <= _ALLOWED_ATTRIBUTES

    dropped = {
        "samantha.scenario_id",
        "samantha.scenario_category",
        "samantha.sweep_run_id",
    }
    present_dropped = dropped & _ALLOWED_ATTRIBUTES
    assert not present_dropped, (
        f"These samantha.* sweep keys must not be on the allowlist: {present_dropped!r}"
    )


# PR207 Low #11: deleted two duplicate membership tests — coverage of
# gen_ai.prompt and gen_ai.completion allowlist membership is owned by
# tests/observability/test_completion_on_spans.py
# (test_slice1_gen_ai_completion_is_on_allowlist and the matching prompt
# test). Retaining them here added no signal and produced redundant CI noise.


def test_set_span_attribute_accepts_every_allowlisted_name() -> None:
    """Deterministic positive case — catches an inverted-predicate flip."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from samantha_server.observability.otel import _ALLOWED_ATTRIBUTES, set_span_attribute

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("t") as span:
        for name in _ALLOWED_ATTRIBUTES:
            # Should not raise.
            set_span_attribute(span, name, "x")


@given(
    name=st.text(
        # Alphabet matches the actual production allowlist — letters,
        # digits, dots, underscores, hyphens. Constrained so Hypothesis
        # spends its budget exploring realistic candidates instead of
        # arbitrary Unicode codepoints that the underlying SDK would
        # reject regardless of our predicate.
        alphabet=string.ascii_letters + string.digits + "._-",
        min_size=1,
        max_size=64,
    )
)
@settings(max_examples=500)
def test_set_span_attribute_rejects_non_allowlisted_names(name: str) -> None:
    """Property test — non-allowlisted names raise."""
    from opentelemetry.sdk.trace import TracerProvider

    from samantha_server.observability.otel import _ALLOWED_ATTRIBUTES, set_span_attribute

    if name in _ALLOWED_ATTRIBUTES:
        return  # filtered; the deterministic test covers these.

    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("t") as span, pytest.raises(ValueError):
        set_span_attribute(span, name, "x")


def test_set_span_attribute_accepts_gen_ai_prompt() -> None:
    """gen_ai.prompt is now allowlisted; set_span_attribute must not raise."""
    from opentelemetry.sdk.trace import TracerProvider

    from samantha_server.observability.otel import set_span_attribute

    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("t") as span:
        # Must not raise.
        set_span_attribute(span, "gen_ai.prompt", "verbatim prompt text")


def test_set_span_attribute_accepts_gen_ai_completion() -> None:
    """gen_ai.completion is unconditionally allowlisted; must not raise."""
    from opentelemetry.sdk.trace import TracerProvider

    from samantha_server.observability.otel import set_span_attribute

    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("t") as span:
        # Must not raise.
        set_span_attribute(span, "gen_ai.completion", "verbatim completion text")
