"""Priority event queue wrapping asyncio.PriorityQueue.

Exports:
- EventPriority: IntEnum defining STAT < ROUTINE < BACKGROUND ordering.
- QueueItem: dataclass ordered by (priority, enqueue_seq); other fields
  carry compare=False so the PriorityQueue heap sorts on those two fields only.
- PriorityEventQueue: bounded queue with FIFO-within-priority, event_id-based
  cancellation, and asyncio.QueueFull back-pressure.

Design decisions (from phase-3-implementation.md Step 2):

G3 — No mid-evaluate preemption. STAT goes to the head of the queue;
     in-flight evaluations are NOT cancelled. task.cancel() is absent
     from this module by design.

G4 — Single global queue. One asyncio.PriorityQueue instance, ordered by
     (priority, enqueue_seq). Three separate queues would let priorities
     run in parallel, which defeats the "STAT blocks ROUTINE" semantic.

Cancellation receipt classification:
     A pre-dequeue cancel emits no EngineDecision and therefore no receipt.
     This is one of two documented no-receipt paths (the other is 503
     back-pressure rejection in Step 3). The cancel() method returns a
     boolean only; no samantha_server.receipts import is present or permitted.

Deterministic-path purity:
     This module must not import samantha_server.{api,llm,observability}.
     The architectural test (test_deterministic_purity.py) enforces this
     by including samantha_server/queue in _REQUIRED_PATHS.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from opentelemetry.context import Context


class EventPriority(IntEnum):
    """Priority levels for queued events.

    Lower integer value = higher priority (asyncio.PriorityQueue is a min-heap).
    STAT preempts ROUTINE: STAT events sort ahead of ROUTINE events already
    in the queue, regardless of enqueue time.
    """

    STAT = 0  # clinical-safety preempt; 503 on STAT is an incident
    ROUTINE = 1  # default priority
    BACKGROUND = 2  # eval-harness replay, batch reprocessing


# Module-level sequence counter. Per-process monotonic counter starting at 0.
# Unbounded Python int — no wrap concern within a process lifetime.
# Resets on restart (queue is drained at shutdown; no in-flight events survive).
_seq_counter: itertools.count[int] = itertools.count()


@dataclass(order=True)
class QueueItem:
    """Heap element for asyncio.PriorityQueue.

    Ordering participates only in (priority, enqueue_seq) — the first two
    fields. All other fields carry compare=False so the dataclass's
    auto-__lt__ is effectively:
        (self.priority, self.enqueue_seq) < (other.priority, other.enqueue_seq)

    Fields
    ------
    priority:
        Integer derived from EventPriority. Lower = higher precedence.
    enqueue_seq:
        Monotonic per-process counter allocated at put() time. Ensures FIFO
        ordering within a priority bucket.
    enqueue_monotonic_ns:
        time.monotonic_ns() captured at put(). Used by Step 4's orchestrator
        to compute queue_wait_us without touching EngineDecision.
    item:
        The opaque payload. Step 4 narrows this to Event.
    event_id:
        Optional unique id allowing pre-dequeue cancellation. Stored on the
        item itself so get() can identify the id without a linear map scan
        (C1/L1 fix).
    otel_context:
        OTel context snapshot captured at put(). Step 8 restores this across
        the queue boundary (contextvars don't propagate across task switches).
        Defaults to None for callers without an active parent span.
    """

    priority: int
    enqueue_seq: int
    enqueue_monotonic_ns: int = field(compare=False)
    item: Any = field(compare=False)
    event_id: str | None = field(default=None, compare=False)
    otel_context: Context | None = field(default=None, compare=False)


class PriorityEventQueue:
    """Bounded priority queue with FIFO-within-bucket and event_id cancellation.

    Wraps asyncio.PriorityQueue so callers get await-able put/get semantics.
    Back-pressure is immediate: put() raises asyncio.QueueFull when full
    (Step 3 converts this to a 503 response).

    All-tombstones behavior: if every queued item has been
    cancelled, get() drains the tombstones and then **blocks waiting for
    the next put()**. This is the intentional event-driven contract — a
    consumer with nothing live to do sleeps until new work arrives. The
    consumer is not "hung"; it is correctly idle. Operators inspecting a
    quiescent system see ``qsize() == 0`` and no consumer-task progress,
    which is the documented quiescent state.

    Parameters
    ----------
    maxsize:
        Maximum number of items the queue can hold. Must be > 0.
        Configured from cfg.QUEUE_BOUND at lifespan startup.
    """

    def __init__(self, *, maxsize: int) -> None:
        self._queue: asyncio.PriorityQueue[QueueItem] = asyncio.PriorityQueue(maxsize=maxsize)
        # event_id → canonical (still-live) QueueItem for that id.
        # The canonical mapping is the single source of truth: a dequeued
        # item is "live" iff `_id_map[event_id] is queue_item`. Cancellation
        # removes the mapping; id reuse (after cancel or after dequeue)
        # rebinds it. C1 + L6 + M4 fold into this design.
        self._id_map: dict[str, QueueItem] = {}
        # Live (non-cancelled, non-stale) item count for qsize().
        self._live_count: int = 0

    async def put(
        self,
        item: Any,
        priority: EventPriority,
        *,
        event_id: str | None = None,
        otel_context: Context | None = None,
    ) -> None:
        """Enqueue *item* at the given *priority*.

        Raises asyncio.QueueFull immediately when the queue is at maxsize.
        Step 3 wraps this as a 503 response — callers must not catch
        QueueFull and silently discard the event.

        Re-using an *event_id* that already maps to a live (non-cancelled,
        non-dequeued) item raises ValueError. The id space is per-process;
        callers are responsible for uniqueness across overlapping puts.
        Reuse after a successful cancel() or dequeue is fine.

        Parameters
        ----------
        item:
            Opaque payload. Step 4 narrows this to Event.
        priority:
            EventPriority level. Lower integer = higher precedence.
        event_id:
            Optional unique id allowing pre-dequeue cancellation via cancel().
            If None, the item cannot be cancelled by id.
        otel_context:
            Optional OTel context snapshot. Step 8 populates this.
        """
        if event_id is not None and event_id in self._id_map:
            raise ValueError(
                f"event_id {event_id!r} is already in use by a live queued "
                "item; cancel() it before reusing the id."
            )

        seq = next(_seq_counter)
        queue_item = QueueItem(
            priority=int(priority),
            enqueue_seq=seq,
            enqueue_monotonic_ns=time.monotonic_ns(),
            item=item,
            event_id=event_id,
            otel_context=otel_context,
        )

        # put_nowait is atomic — no TOCTOU window between
        # full() check and put. Raises asyncio.QueueFull at the boundary.
        # _live_count and _id_map mutations follow the successful put.
        self._queue.put_nowait(queue_item)

        if event_id is not None:
            self._id_map[event_id] = queue_item
        self._live_count += 1

    async def get(self) -> QueueItem:
        """Dequeue and return the highest-priority live item.

        Silently drains stale items — those whose event_id no longer
        maps to this specific queue_item in `_id_map` (cancelled, or
        replaced by an id reuse after a previous cancel/dequeue).
        Blocks until a live item is available — including the
        all-tombstones case, where the consumer waits for the next put()
        (intentional; see class docstring).

        Removes the event_id map entry for the returned item so subsequent
        cancel() calls return False.
        """
        while True:
            queue_item = await self._queue.get()
            event_id = queue_item.event_id

            if event_id is None:
                # Anonymous put: always live (cannot be cancelled by id).
                self._live_count -= 1
                return queue_item

            canonical = self._id_map.get(event_id)
            if canonical is queue_item:
                # Live: this is still the canonical item for this id.
                self._id_map.pop(event_id)
                self._live_count -= 1
                return queue_item

            # Stale: cancelled (id removed from map) or replaced by a
            # later put with the same id. Drain silently; do NOT decrement
            # _live_count — cancel() already did when the mapping was
            # removed.

    def cancel(self, event_id: str) -> bool:
        """Cancel a queued event by id before it dequeues.

        Removes the canonical mapping for *event_id*; the next get() that
        dequeues the item will treat it as stale and drain it silently.
        No EngineDecision is produced; no receipt is emitted (documented
        no-receipt path).

        No task.cancel() is called here — Decision G3 prohibits
        mid-evaluate preemption.

        Parameters
        ----------
        event_id:
            The id passed to put(). Unknown ids (never enqueued, already
            dequeued, or already cancelled) return False.

        Returns
        -------
        bool
            True if the event was found and cancelled.
            False if the event_id is unknown, already dequeued, or
            already cancelled. Removing the canonical mapping in this
            method (rather than waiting for get() to drain) is what makes
            double-cancel return False.
        """
        if event_id not in self._id_map:
            return False
        self._id_map.pop(event_id)
        self._live_count -= 1
        return True

    def qsize(self) -> int:
        """Return the number of live (non-cancelled) items in the queue."""
        return self._live_count

    @property
    def slot_count(self) -> int:
        """Return the number of occupied heap slots, including cancelled tombstones.

        ``put_nowait`` raises ``asyncio.QueueFull`` based on slot occupancy
        (the underlying ``asyncio.PriorityQueue.qsize()``), not on
        ``_live_count``. Step 3's back-pressure 503 reports this value as
        the observed depth so it equals ``maxsize`` at rejection time
        regardless of how many tombstones the heap is carrying.
        """
        return self._queue.qsize()

    @property
    def maxsize(self) -> int:
        """Return the configured maximum queue capacity (always ``> 0``).

        ``__init__`` does not validate ``maxsize > 0``; callers
        (``build_app_state``) rely on ``cfg.QUEUE_BOUND``'s eager guard
        in ``samantha_server/config.py``.
        """
        return self._queue.maxsize


__all__ = ["EventPriority", "PriorityEventQueue", "QueueItem"]
