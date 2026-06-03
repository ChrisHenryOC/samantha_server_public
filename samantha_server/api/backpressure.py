"""Back-pressure helper for the Phase 3 Step 3 503 surface.

Exports:
- BackpressureRejection: typed exception carrying rejection metadata.
- enqueue_or_reject: async helper; raises BackpressureRejection on asyncio.QueueFull.
- to_503_response: synchronous FastAPI response builder for BackpressureRejection.

Design decisions:
- G5 (synchronous fail-fast): no server-side retry / wait_for timeout.
- G6 (STAT respects bound): no priority bypass; a STAT 503 is an incident.
- No-receipt path: back-pressure rejection emits no EngineDecision and therefore
  no receipt. See samantha_server.api.no_receipt_paths for the closed allow-list.
- PHI boundary: BackpressureRejection must NOT carry the rejected event payload.
  to_503_response() accepts only a BackpressureRejection — the signature enforces
  the boundary structurally.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Final

import fastapi.responses
from opentelemetry.context import Context

from samantha_server.observability.counters import CounterRegistry
from samantha_server.queue.priority import EventPriority, PriorityEventQueue

# Body-schema constants — Step 4's POST /events consumer parses against these.
_BODY_ERROR_CODE: Final[str] = "backpressure"
_BODY_MESSAGE: Final[str] = "queue full; retry after the indicated interval"


@dataclass(frozen=True)
class BackpressureRejection(Exception):
    """Raised by enqueue_or_reject when the queue is full.

    Fields
    ------
    bound:
        The configured queue maxsize (from queue.maxsize).
    depth:
        Heap-slot occupancy observed at rejection time (from
        ``queue.slot_count``). At rejection time this equals ``bound``
        by construction — ``put_nowait`` only raises ``QueueFull`` when
        the heap is at ``maxsize``. Reported in the 503 body so an
        operator can correlate with ``queue_overflow_rejections`` without
        having to also fetch ``QUEUE_BOUND`` from config.
    retry_after_sec:
        Seconds the caller should wait before retrying. Passed through
        to the Retry-After HTTP header by to_503_response.

    Must NOT carry the rejected event payload — PHI boundary.

    The class is a frozen dataclass *and* an Exception subclass.
    ``__post_init__`` calls ``Exception.__init__(bound, depth, retry_after_sec)``
    so ``exc.args`` and ``str(exc)`` carry the rejection metadata for
    standard logging handlers (otherwise both would be empty — frozen
    dataclasses don't auto-populate ``Exception.args``).
    """

    bound: int
    depth: int
    retry_after_sec: int

    def __post_init__(self) -> None:
        # ``Exception.__init__`` writes to the BaseException ``args`` slot,
        # which is independent of the frozen-dataclass attribute lock — no
        # FrozenInstanceError here.
        Exception.__init__(self, self.bound, self.depth, self.retry_after_sec)


async def enqueue_or_reject(
    queue: PriorityEventQueue,
    *,
    item: Any,
    priority: EventPriority,
    counters: CounterRegistry,
    retry_after_sec: int,
    event_id: str | None = None,
    otel_context: Context | None = None,
) -> None:
    """Enqueue *item* or raise BackpressureRejection if the queue is full.

    Calls queue.put(). On asyncio.QueueFull:
      1. Raises BackpressureRejection with bound and slot_count from the queue.
      2. Increments counters.queue_overflow_rejections.

    Order matters: the typed exception is raised **first**, then the counter
    is incremented in the same ``except`` block via ``finally``-style
    sequencing. A throwing increment must not mask the typed exception that
    callers (Step 4's POST /events handler) catch to build the 503.

    Back-pressure is synchronous (G5): no wait_for, no server-side retry loop.
    STAT priority is subject to the same bound as ROUTINE (G6).

    The function is ``async`` because ``PriorityEventQueue.put`` is ``async``,
    even though neither suspends in practice today (``put`` calls ``put_nowait``
    internally with no ``await``). Adding any ``await`` to the body — e.g.,
    ``asyncio.wait_for(queue.put(...), timeout=...)`` — would silently break
    G5 and must be reviewed against the synchronous-fail-fast contract.

    Parameters
    ----------
    queue:
        The bounded priority queue.
    item:
        Opaque payload to enqueue. Typed ``Any`` to match
        ``QueueItem.item: Any``. Not stored on BackpressureRejection (PHI).
    priority:
        EventPriority level; passed through to queue.put.
    counters:
        CounterRegistry; queue_overflow_rejections is incremented on rejection.
    retry_after_sec:
        Seconds to suggest in Retry-After on rejection.
    event_id:
        Optional unique id for pre-dequeue cancellation; forwarded to queue.put.
    otel_context:
        Optional OTel context snapshot; forwarded to queue.put.

    Raises
    ------
    BackpressureRejection:
        Queue at maxsize; the synchronous fail-fast surface.
    ValueError:
        ``queue.put`` reuses-event_id guard fired. Propagates unchanged —
        this is a programming error in the caller, not a back-pressure
        signal, so no counter is incremented and no 503 is appropriate.
        Step 4's POST /events handler is the call-site of record for
        deciding how to surface this (typically 422 / 500 depending on
        whether the id came from the request).
    """
    try:
        await queue.put(item, priority, event_id=event_id, otel_context=otel_context)
    except asyncio.QueueFull:
        rejection = BackpressureRejection(
            bound=queue.maxsize,
            depth=queue.slot_count,
            retry_after_sec=retry_after_sec,
        )
        counters.queue_overflow_rejections.increment()
        raise rejection from None


def to_503_response(rejection: BackpressureRejection) -> fastapi.responses.JSONResponse:
    """Build a 503 JSONResponse for a BackpressureRejection.

    Sets the Retry-After header and a structured body with queue metadata.
    The body contains no event payload — the function's signature enforces
    the PHI boundary: only a BackpressureRejection (which must not carry the
    payload) is accepted.

    Body schema (keys are module-level constants for Step 4's parser):
        {
          "error": "backpressure",
          "message": "queue full; retry after the indicated interval",
          "queue_bound": <int>,
          "queue_depth": <int>,
          "retry_after_sec": <int>
        }
    """
    return fastapi.responses.JSONResponse(
        status_code=503,
        content={
            "error": _BODY_ERROR_CODE,
            "message": _BODY_MESSAGE,
            "queue_bound": rejection.bound,
            "queue_depth": rejection.depth,
            "retry_after_sec": rejection.retry_after_sec,
        },
        headers={"Retry-After": str(rejection.retry_after_sec)},
    )


__all__ = ["BackpressureRejection", "enqueue_or_reject", "to_503_response"]
