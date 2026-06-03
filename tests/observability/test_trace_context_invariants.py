"""Tests for TraceContext __post_init__ mutual-exclusivity invariant.

GH-183 Slice 4: session_id and scenario_id are mutually exclusive.
No real call site ever needs both; conflating them produces inconsistent
spans (both scenario_id and session_id would write langfuse.trace.name /
langfuse.trace.metadata.scenario_id with different precedence semantics).

Flagged as L3 in PR #186 workflow-logic-reviewer review.
"""

from __future__ import annotations

import pytest


def test_trace_context_only_session_id_succeeds() -> None:
    """Setting only session_id: construction succeeds."""
    from samantha_server.observability.trace_context import TraceContext

    ctx = TraceContext(session_id="sess-001")
    assert ctx.session_id == "sess-001"
    assert ctx.scenario_id is None


def test_trace_context_only_scenario_id_succeeds() -> None:
    """Setting only scenario_id: construction succeeds."""
    from samantha_server.observability.trace_context import TraceContext

    ctx = TraceContext(scenario_id="SC-001")
    assert ctx.scenario_id == "SC-001"
    assert ctx.session_id is None


def test_trace_context_neither_set_succeeds() -> None:
    """Setting neither session_id nor scenario_id: construction succeeds.

    Some callers stamp a minimal context (e.g., environment-only pre-dispatch).
    """
    from samantha_server.observability.trace_context import TraceContext

    ctx = TraceContext()
    assert ctx.session_id is None
    assert ctx.scenario_id is None


def test_trace_context_both_set_raises_value_error() -> None:
    """Setting both session_id and scenario_id raises ValueError.

    The error message must name both fields so the caller knows which to remove.
    """
    from samantha_server.observability.trace_context import TraceContext

    with pytest.raises(ValueError) as exc_info:
        TraceContext(session_id="sess-001", scenario_id="SC-001")

    message = str(exc_info.value)
    assert "session_id" in message, f"Error message must name 'session_id'; got: {message!r}"
    assert "scenario_id" in message, f"Error message must name 'scenario_id'; got: {message!r}"
    assert "mutually exclusive" in message.lower(), (
        f"Error message should explain mutual exclusivity; got: {message!r}"
    )
