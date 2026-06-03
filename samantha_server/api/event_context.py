"""EventDispatchContext — orchestrator-owned per-event context.

Carries orchestrator-side fields that must NOT pollute EngineDecision:
queue wait time, routing classification, session identity, and dispatch
token. Step 10's trace serializer reads these fields; the deterministic
engine never sees them.

Deterministic-path purity: this module lives under api/ and may import
from queue/. Engine modules (engine/, rules/, primitives/, models/) must
not import from api/.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from samantha_server.queue.priority import EventPriority


@dataclass(frozen=True)
class EventDispatchContext:
    """Orchestrator-owned context for a single dispatched event.

    Created by dispatch_event() after it determines the routing path
    and before it calls the handler. Step 10's trace serializer reads
    routing_path and queue_wait_us from this object, never from
    EngineDecision.

    Fields
    ------
    session_id:
        Caller-supplied session identifier.
    priority:
        EventPriority level at enqueue time.
    routing_path:
        'llm' if the event was handled by an LLM handler;
        'deterministic' if handled by the rules engine evaluate() path.
    queue_wait_us:
        Time the event spent in the priority queue, in microseconds.
        Computed by the consumer as (dequeue_ns - enqueue_ns) // 1000.
    dispatch_token_id:
        Step 5 will populate this with the HMAC dispatch token id.
        ``None`` means Step 5 has not run — i.e., the event was dispatched
        without a dispatch token (pre-Step-5 path or test-factory path).
        A non-None value indicates the token id was present and validated.
        The ``None`` vs non-None distinction is intentional, not accidental;
        use a sentinel object only if a third "present but invalid" state
        is ever needed.
    """

    session_id: str
    priority: EventPriority
    routing_path: Literal["deterministic", "llm"]
    queue_wait_us: int
    dispatch_token_id: str | None = None


__all__ = ["EventDispatchContext"]
