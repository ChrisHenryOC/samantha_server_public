"""Tests for samantha_server.api.event_context.EventDispatchContext."""

from __future__ import annotations

import pytest


def test_event_dispatch_context_fields_present() -> None:
    """EventDispatchContext has the required fields and accepts valid values."""
    from samantha_server.api.event_context import EventDispatchContext
    from samantha_server.queue.priority import EventPriority

    ctx = EventDispatchContext(
        session_id="sess-001",
        priority=EventPriority.ROUTINE,
        routing_path="llm",
        queue_wait_us=500,
    )
    assert ctx.session_id == "sess-001"
    assert ctx.priority == EventPriority.ROUTINE
    assert ctx.routing_path == "llm"
    assert ctx.queue_wait_us == 500
    assert ctx.dispatch_token_id is None


def test_event_dispatch_context_is_frozen() -> None:
    """EventDispatchContext is immutable (frozen dataclass)."""
    from samantha_server.api.event_context import EventDispatchContext
    from samantha_server.queue.priority import EventPriority

    ctx = EventDispatchContext(
        session_id="sess-001",
        priority=EventPriority.STAT,
        routing_path="deterministic",
        queue_wait_us=0,
    )
    with pytest.raises((AttributeError, TypeError)):
        ctx.session_id = "other"  # type: ignore[misc]


def test_event_dispatch_context_dispatch_token_id_default_none() -> None:
    """dispatch_token_id defaults to None."""
    from samantha_server.api.event_context import EventDispatchContext
    from samantha_server.queue.priority import EventPriority

    ctx = EventDispatchContext(
        session_id="s",
        priority=EventPriority.BACKGROUND,
        routing_path="llm",
        queue_wait_us=100,
    )
    assert ctx.dispatch_token_id is None


def test_event_dispatch_context_dispatch_token_id_settable() -> None:
    """dispatch_token_id can be set to a string value."""
    from samantha_server.api.event_context import EventDispatchContext
    from samantha_server.queue.priority import EventPriority

    ctx = EventDispatchContext(
        session_id="s",
        priority=EventPriority.ROUTINE,
        routing_path="deterministic",
        queue_wait_us=42,
        dispatch_token_id="tok-abc",
    )
    assert ctx.dispatch_token_id == "tok-abc"


def test_event_dispatch_context_routing_path_values() -> None:
    """routing_path accepts 'deterministic' and 'llm'."""
    from samantha_server.api.event_context import EventDispatchContext
    from samantha_server.queue.priority import EventPriority

    for path in ("deterministic", "llm"):
        ctx = EventDispatchContext(
            session_id="s",
            priority=EventPriority.ROUTINE,
            routing_path=path,  # type: ignore[arg-type]
            queue_wait_us=0,
        )
        assert ctx.routing_path == path


def test_event_dispatch_context_all_priority_values() -> None:
    """EventDispatchContext accepts all three EventPriority levels."""
    from samantha_server.api.event_context import EventDispatchContext
    from samantha_server.queue.priority import EventPriority

    for prio in (EventPriority.STAT, EventPriority.ROUTINE, EventPriority.BACKGROUND):
        ctx = EventDispatchContext(
            session_id="s",
            priority=prio,
            routing_path="llm",
            queue_wait_us=0,
        )
        assert ctx.priority == prio
