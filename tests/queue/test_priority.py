"""Tests for samantha_server.queue.priority — PriorityEventQueue behavior.

Coverage:
- Priority ordering: STAT dequeues before ROUTINE regardless of enqueue order.
- FIFO within a priority bucket (enqueue_seq guarantees stable ordering).
- Cancellation by event_id: removes event before it dequeues.
- Cancellation returns True when found, False when not found.
- Cancellation after dequeue returns False.
- qsize() reflects current queue depth.
- put() raises asyncio.QueueFull when maxsize is reached.
- Cancelled events produce no QueueItem from get() (receipt path never reached).
- Double-cancel returns False; _id_map does not leak.
- All-tombstones get() blocks waiting for next put.
- QueueItem.__lt__ sorts by (priority, enqueue_seq) only.
- BACKGROUND priority sorts after ROUTINE.
- event_id reuse after cancel delivers new put live.
"""

from __future__ import annotations

import asyncio

import pytest

# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


def test_stat_dequeues_before_queued_routine() -> None:
    """STAT enqueued after ROUTINE still dequeues first.

    Decision G4: single queue ordered by (priority, enqueue_seq).
    STAT = 0 < ROUTINE = 1, so STAT sorts ahead regardless of sequence.
    """
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("routine-1", EventPriority.ROUTINE)
        await q.put("routine-2", EventPriority.ROUTINE)
        await q.put("stat-1", EventPriority.STAT)

        first = await q.get()
        assert first.item == "stat-1", f"Expected stat-1 first, got {first.item!r}"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# FIFO within priority bucket
# ---------------------------------------------------------------------------


def test_fifo_within_priority_bucket() -> None:
    """Three ROUTINE events dequeue in the same order they were enqueued.

    enqueue_seq is the FIFO tiebreaker within a priority bucket.
    """
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("a", EventPriority.ROUTINE)
        await q.put("b", EventPriority.ROUTINE)
        await q.put("c", EventPriority.ROUTINE)

        first = await q.get()
        second = await q.get()
        third = await q.get()

        assert first.item == "a"
        assert second.item == "b"
        assert third.item == "c"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Cancellation — cancel() return value semantics
# ---------------------------------------------------------------------------


def test_cancel_returns_true_when_event_id_found() -> None:
    """cancel(event_id) returns True when the event is still in the queue.

    The subsequent get() must not return the cancelled item.
    """
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("payload", EventPriority.ROUTINE, event_id="evt-1")
        await q.put("other", EventPriority.ROUTINE)

        result = q.cancel("evt-1")
        assert result is True

        # Only "other" should remain
        item = await q.get()
        assert item.item == "other"
        assert q.qsize() == 0

    asyncio.run(run())


def test_cancel_returns_false_when_event_id_unknown() -> None:
    """cancel() returns False when the id was never enqueued."""
    from samantha_server.queue.priority import PriorityEventQueue

    q = PriorityEventQueue(maxsize=10)
    result = q.cancel("never-existed")
    assert result is False


def test_cancel_after_dequeue_returns_false() -> None:
    """cancel() returns False when the event has already been dequeued.

    The internal map entry is removed on get(), so a post-get cancel
    sees an unknown id.
    """
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("payload", EventPriority.ROUTINE, event_id="evt-dequeued")

        _item = await q.get()  # dequeue first
        result = q.cancel("evt-dequeued")
        assert result is False

    asyncio.run(run())


# ---------------------------------------------------------------------------
# qsize
# ---------------------------------------------------------------------------


def test_qsize_reflects_current_count() -> None:
    """qsize() returns the number of items available to get()."""
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        assert q.qsize() == 0

        await q.put("a", EventPriority.ROUTINE)
        assert q.qsize() == 1

        await q.put("b", EventPriority.STAT)
        assert q.qsize() == 2

        await q.get()
        assert q.qsize() == 1

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Back-pressure: bounded queue raises QueueFull
# ---------------------------------------------------------------------------


def test_put_raises_queue_full_when_at_maxsize() -> None:
    """The third put on a maxsize=2 queue raises asyncio.QueueFull.

    Step 3 wraps this as a 503. Step 2 verifies the raw exception
    surfaces rather than silently dropping the event.
    """
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=2)
        await q.put("a", EventPriority.ROUTINE)
        await q.put("b", EventPriority.ROUTINE)

        with pytest.raises(asyncio.QueueFull):
            await q.put("c", EventPriority.ROUTINE)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Cancelled events produce no _QueueItem (no receipt path reached)
# ---------------------------------------------------------------------------


def test_cancellation_produces_no_queue_item_from_get() -> None:
    """A cancelled event never surfaces from get().

    This verifies the cancel path does not silently enqueue a
    tombstone that a consumer could dequeue. The queue module has
    no samantha_server.receipts import (architectural test enforces
    this); this test asserts the behavioral contract from Step 2's
    design doc.
    """
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("to-cancel", EventPriority.ROUTINE, event_id="cancel-me")
        await q.put("to-keep", EventPriority.ROUTINE)

        q.cancel("cancel-me")

        item = await q.get()
        assert item.item == "to-keep"
        assert q.qsize() == 0  # nothing else queued

    asyncio.run(run())


# ---------------------------------------------------------------------------
# _QueueItem fields (enqueue_monotonic_ns, otel_context)
# ---------------------------------------------------------------------------


def test_queue_item_has_enqueue_monotonic_ns() -> None:
    """_QueueItem.enqueue_monotonic_ns is set at put() time."""
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("payload", EventPriority.STAT)
        item = await q.get()
        assert isinstance(item.enqueue_monotonic_ns, int)
        assert item.enqueue_monotonic_ns > 0

    asyncio.run(run())


def test_queue_item_otel_context_defaults_none() -> None:
    """_QueueItem.otel_context defaults to None when not provided."""
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("payload", EventPriority.ROUTINE)
        item = await q.get()
        assert item.otel_context is None

    asyncio.run(run())


def test_queue_item_otel_context_set_when_provided() -> None:
    """_QueueItem.otel_context carries the value passed to put()."""
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        sentinel = object()
        q = PriorityEventQueue(maxsize=10)
        await q.put("payload", EventPriority.ROUTINE, otel_context=sentinel)
        item = await q.get()
        assert item.otel_context is sentinel

    asyncio.run(run())


# ---------------------------------------------------------------------------
# qsize after cancel (cancelled items must not count)
# ---------------------------------------------------------------------------


def test_qsize_excludes_cancelled_items() -> None:
    """qsize() does not count items that have been cancelled.

    After cancel, the consumer will skip the tombstoned item.
    Reporting it in qsize() would mislead back-pressure logic.
    """
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("a", EventPriority.ROUTINE, event_id="cancel-a")
        await q.put("b", EventPriority.ROUTINE)
        assert q.qsize() == 2

        q.cancel("cancel-a")
        # After cancel, qsize should reflect 1 live item, not 2
        assert q.qsize() == 1

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Double-cancel + _id_map leak
# ---------------------------------------------------------------------------


def test_double_cancel_returns_false_and_does_not_decrement() -> None:
    """Cancelling the same id twice does not double-decrement _live_count.

     C1 regression: previously the second cancel found a stale
    _id_map entry (because get's tombstone drain only removed the id from
    _cancelled, not _id_map), so double-cancel re-added to _cancelled and
    decremented _live_count again — qsize() went negative.
    """
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("payload", EventPriority.ROUTINE, event_id="evt")
        await q.put("other", EventPriority.ROUTINE)

        assert q.cancel("evt") is True
        assert q.cancel("evt") is False
        assert q.qsize() == 1  # would be 0 (or less!) under the C1 bug

        # Drain the tombstone via get() and confirm a third cancel is still False.
        item = await q.get()
        assert item.item == "other"
        assert q.cancel("evt") is False
        assert q.qsize() == 0

    asyncio.run(run())


def test_id_map_does_not_leak_after_tombstone_drain() -> None:
    """After get() drains a tombstone, the _id_map entry is also removed.

     C1: previously _id_map kept the stale entry, allowing
    double-cancel to corrupt _live_count.
    """
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("payload", EventPriority.ROUTINE, event_id="evt")
        await q.put("other", EventPriority.ROUTINE)

        q.cancel("evt")
        # Drain the tombstone (get returns the live "other" item).
        item = await q.get()
        assert item.item == "other"

        # Internal invariant: id mapping is gone after cancel + drain.
        # (Identity-based design eliminates the _cancelled set entirely;
        # _id_map is the single source of truth.)
        assert "evt" not in q._id_map

    asyncio.run(run())


# ---------------------------------------------------------------------------
# event_id reuse after cancel
# ---------------------------------------------------------------------------


def test_event_id_reuse_after_cancel_delivers_new_put_live() -> None:
    """After cancelling id X, a new put with id X is delivered live, not tombstoned.

     L6: put must clear any stale tombstone from a prior cancelled
    put with the same id; otherwise get() skips the new event.
    """
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("first", EventPriority.ROUTINE, event_id="reused-id")
        q.cancel("reused-id")
        # Re-put with the same id (without first draining the tombstone via get).
        await q.put("second", EventPriority.ROUTINE, event_id="reused-id")

        # The next get() must drain the tombstoned "first" and return "second".
        item = await q.get()
        assert item.item == "second"
        assert q.qsize() == 0

    asyncio.run(run())


# ---------------------------------------------------------------------------
# All-tombstones get() blocks waiting for next put
# ---------------------------------------------------------------------------


def test_all_tombstones_get_blocks_until_next_put() -> None:
    """When every queued item is cancelled, get() blocks (does not raise).

     C2: this is the documented event-driven contract — a consumer
    with nothing live to process sleeps until new work arrives. The test
    locks in the contract: a 50 ms wait_for times out (i.e., get blocks);
    a subsequent put unblocks the await.
    """
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("a", EventPriority.ROUTINE, event_id="cancel-a")
        await q.put("b", EventPriority.ROUTINE, event_id="cancel-b")
        q.cancel("cancel-a")
        q.cancel("cancel-b")

        # Tombstones drain first, then the underlying queue is empty.
        # get() correctly blocks waiting for the next put.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q.get(), timeout=0.05)

        # A subsequent put unblocks a fresh get() — the queue is healthy,
        # not deadlocked.
        await q.put("c", EventPriority.ROUTINE)
        item = await asyncio.wait_for(q.get(), timeout=0.5)
        assert item.item == "c"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# QueueItem.__lt__ orders by (priority, enqueue_seq) only
# ---------------------------------------------------------------------------


def test_queueitem_lt_orders_by_priority_and_seq_only() -> None:
    """QueueItem.__lt__ ignores compare=False fields.

     H1 regression: a drive-by edit removing compare=False from
    enqueue_monotonic_ns / item / event_id / otel_context would silently
    break FIFO ordering and STAT-preempts-ROUTINE. This test makes the
    invariant explicit at the dataclass boundary, independent of queue
    behavior.
    """
    from samantha_server.queue.priority import QueueItem

    a = QueueItem(
        priority=1,
        enqueue_seq=42,
        enqueue_monotonic_ns=100,
        item="alpha",
        event_id="id-a",
        otel_context=object(),
    )
    b = QueueItem(
        priority=1,
        enqueue_seq=42,
        enqueue_monotonic_ns=999_999_999,  # different
        item="beta",  # different
        event_id="id-b",  # different
        otel_context=object(),  # different
    )
    # With (priority, enqueue_seq) identical, neither a<b nor b<a should hold.
    assert (a < b) is False
    assert (b < a) is False


# ---------------------------------------------------------------------------
# BACKGROUND priority coverage
# ---------------------------------------------------------------------------


def test_routine_dequeues_before_background() -> None:
    """ROUTINE (1) sorts ahead of BACKGROUND (2)."""
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("bg", EventPriority.BACKGROUND)
        await q.put("rt", EventPriority.ROUTINE)

        first = await q.get()
        assert first.item == "rt"

    asyncio.run(run())


def test_stat_preempts_background_too() -> None:
    """STAT (0) sorts ahead of BACKGROUND (2) regardless of enqueue order."""
    from samantha_server.queue.priority import EventPriority, PriorityEventQueue

    async def run() -> None:
        q = PriorityEventQueue(maxsize=10)
        await q.put("bg", EventPriority.BACKGROUND)
        await q.put("stat", EventPriority.STAT)

        first = await q.get()
        assert first.item == "stat"

    asyncio.run(run())
