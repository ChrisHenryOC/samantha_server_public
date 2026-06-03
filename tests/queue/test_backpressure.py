"""Tests for samantha_server.api.backpressure — back-pressure helper behavior.

Coverage:
- BackpressureRejection carries bound, depth, retry_after_sec.
- enqueue_or_reject happy path: item enqueued, no counter increment.
- enqueue_or_reject rejection path: raises BackpressureRejection, counter incremented.
- STAT priority respects bound exactly like ROUTINE (G6).
- to_503_response: status 503, Retry-After header, correct body keys, no PHI.
- Concurrent submission race: heap occupancy never exceeds bound; counter
  matches the rejection count.
"""

from __future__ import annotations

import asyncio

import pytest

from samantha_server.observability.counters import CounterRegistry
from samantha_server.queue.priority import EventPriority, PriorityEventQueue

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _make_full_queue(maxsize: int = 2) -> PriorityEventQueue:
    """Return a PriorityEventQueue at maxsize (all slots occupied).

    The helper is ``async`` because ``PriorityEventQueue.put`` is ``async``;
    callers invoke it from inside an ``async def run()`` block driven by
    ``asyncio.run`` — the standard pattern in this test file.
    """
    q = PriorityEventQueue(maxsize=maxsize)
    for i in range(maxsize):
        await q.put(f"filler-{i}", EventPriority.ROUTINE)
    return q


# ---------------------------------------------------------------------------
# Slice 1 — BackpressureRejection carries bound / depth / retry_after_sec
# ---------------------------------------------------------------------------


def test_backpressure_rejection_carries_expected_fields() -> None:
    """BackpressureRejection stores bound, depth, and retry_after_sec."""
    from samantha_server.api.backpressure import BackpressureRejection

    exc = BackpressureRejection(bound=256, depth=256, retry_after_sec=5)

    assert exc.bound == 256
    assert exc.depth == 256
    assert exc.retry_after_sec == 5


def test_backpressure_rejection_is_exception() -> None:
    """BackpressureRejection is raise-able as an Exception."""
    from samantha_server.api.backpressure import BackpressureRejection

    with pytest.raises(BackpressureRejection) as exc_info:
        raise BackpressureRejection(bound=10, depth=10, retry_after_sec=3)

    assert exc_info.value.bound == 10


# ---------------------------------------------------------------------------
# Slice 2 — enqueue_or_reject happy path
# ---------------------------------------------------------------------------


def test_enqueue_or_reject_happy_path_enqueues_item() -> None:
    """With depth < bound, the item is placed on the queue; qsize grows by 1."""
    from samantha_server.api.backpressure import enqueue_or_reject

    async def run() -> None:
        q = PriorityEventQueue(maxsize=4)
        counters = CounterRegistry()
        initial_size = q.qsize()

        await enqueue_or_reject(
            q,
            item="test-payload",
            priority=EventPriority.ROUTINE,
            counters=counters,
            retry_after_sec=5,
        )

        assert q.qsize() == initial_size + 1

    asyncio.run(run())


def test_enqueue_or_reject_happy_path_does_not_increment_counter() -> None:
    """Successful enqueue must not increment queue_overflow_rejections."""
    from samantha_server.api.backpressure import enqueue_or_reject

    async def run() -> None:
        q = PriorityEventQueue(maxsize=4)
        counters = CounterRegistry()

        await enqueue_or_reject(
            q,
            item="test-payload",
            priority=EventPriority.ROUTINE,
            counters=counters,
            retry_after_sec=5,
        )

        assert counters.queue_overflow_rejections.value == 0

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Slice 3 — rejection path: raises BackpressureRejection, increments counter
# ---------------------------------------------------------------------------


def test_enqueue_or_reject_raises_when_queue_full() -> None:
    """When queue is at maxsize, enqueue_or_reject raises BackpressureRejection."""
    from samantha_server.api.backpressure import BackpressureRejection, enqueue_or_reject

    async def run() -> None:
        q = await _make_full_queue(maxsize=2)
        counters = CounterRegistry()

        with pytest.raises(BackpressureRejection):
            await enqueue_or_reject(
                q,
                item="rejected",
                priority=EventPriority.ROUTINE,
                counters=counters,
                retry_after_sec=5,
            )

    asyncio.run(run())


def test_enqueue_or_reject_rejection_bound_matches_maxsize() -> None:
    """BackpressureRejection.bound equals the queue maxsize."""
    from samantha_server.api.backpressure import BackpressureRejection, enqueue_or_reject

    maxsize = 3

    async def run() -> None:
        q = await _make_full_queue(maxsize=maxsize)
        counters = CounterRegistry()

        with pytest.raises(BackpressureRejection) as exc_info:
            await enqueue_or_reject(
                q,
                item="rejected",
                priority=EventPriority.ROUTINE,
                counters=counters,
                retry_after_sec=5,
            )

        assert exc_info.value.bound == maxsize

    asyncio.run(run())


def test_enqueue_or_reject_rejection_depth_equals_bound_at_rejection() -> None:
    """BackpressureRejection.depth equals slot_count (heap occupancy) at rejection time.

    enqueue_or_reject reads queue.slot_count (heap slots, including tombstones)
    rather than qsize() (live count). On a freshly-filled queue with no
    cancellations both values equal maxsize, but the contract is slot occupancy —
    the same metric that asyncio.QueueFull fires on.
    """
    from samantha_server.api.backpressure import BackpressureRejection, enqueue_or_reject

    maxsize = 2

    async def run() -> None:
        q = await _make_full_queue(maxsize=maxsize)
        counters = CounterRegistry()
        slot_depth_before = q.slot_count

        with pytest.raises(BackpressureRejection) as exc_info:
            await enqueue_or_reject(
                q,
                item="rejected",
                priority=EventPriority.ROUTINE,
                counters=counters,
                retry_after_sec=5,
            )

        assert exc_info.value.depth == slot_depth_before

    asyncio.run(run())


def test_enqueue_or_reject_rejected_item_not_in_queue() -> None:
    """The rejected item is NOT present in the queue after rejection."""
    from samantha_server.api.backpressure import BackpressureRejection, enqueue_or_reject

    async def run() -> None:
        q = await _make_full_queue(maxsize=2)
        counters = CounterRegistry()
        size_before = q.qsize()

        with pytest.raises(BackpressureRejection):
            await enqueue_or_reject(
                q,
                item="rejected-payload",
                priority=EventPriority.ROUTINE,
                counters=counters,
                retry_after_sec=5,
            )

        # Queue size must not have grown
        assert q.qsize() == size_before

    asyncio.run(run())


def test_enqueue_or_reject_rejection_increments_counter() -> None:
    """Rejection increments counters.queue_overflow_rejections by exactly 1."""
    from samantha_server.api.backpressure import BackpressureRejection, enqueue_or_reject

    async def run() -> None:
        q = await _make_full_queue(maxsize=2)
        counters = CounterRegistry()

        with pytest.raises(BackpressureRejection):
            await enqueue_or_reject(
                q,
                item="rejected",
                priority=EventPriority.ROUTINE,
                counters=counters,
                retry_after_sec=5,
            )

        assert counters.queue_overflow_rejections.value == 1

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Slice 4 — STAT priority respects bound (G6)
# ---------------------------------------------------------------------------


def test_stat_event_rejected_when_queue_full() -> None:
    """G6: a STAT event submitted to a full queue is rejected like ROUTINE.

    No priority bypass for back-pressure — a STAT 503 is an incident.
    """
    from samantha_server.api.backpressure import BackpressureRejection, enqueue_or_reject

    async def run() -> None:
        q = await _make_full_queue(maxsize=2)
        counters = CounterRegistry()

        with pytest.raises(BackpressureRejection):
            await enqueue_or_reject(
                q,
                item="stat-event",
                priority=EventPriority.STAT,
                counters=counters,
                retry_after_sec=5,
            )

        assert counters.queue_overflow_rejections.value == 1

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Slice 5 — to_503_response: status 503, Retry-After header, body shape, PHI safety
# ---------------------------------------------------------------------------


def test_to_503_response_status_is_503() -> None:
    """to_503_response returns status_code 503."""
    from samantha_server.api.backpressure import BackpressureRejection, to_503_response

    rejection = BackpressureRejection(bound=256, depth=256, retry_after_sec=5)
    response = to_503_response(rejection)

    assert response.status_code == 503


def test_to_503_response_retry_after_header() -> None:
    """to_503_response includes Retry-After header matching retry_after_sec."""
    from samantha_server.api.backpressure import BackpressureRejection, to_503_response

    rejection = BackpressureRejection(bound=256, depth=256, retry_after_sec=7)
    response = to_503_response(rejection)

    assert response.headers["Retry-After"] == "7"


def test_to_503_response_body_contains_required_keys() -> None:
    """Response body contains error, message, queue_bound, queue_depth, retry_after_sec."""
    import json

    from samantha_server.api.backpressure import BackpressureRejection, to_503_response

    rejection = BackpressureRejection(bound=100, depth=100, retry_after_sec=5)
    response = to_503_response(rejection)

    body = json.loads(response.body)

    assert body["error"] == "backpressure"
    assert "message" in body
    assert body["queue_bound"] == 100
    assert body["queue_depth"] == 100
    assert body["retry_after_sec"] == 5


def test_to_503_response_signature_excludes_event_payload() -> None:
    """to_503_response accepts only a BackpressureRejection — no event payload parameter.

    This enforces the PHI boundary structurally: the function signature
    makes it impossible to accidentally echo event data into the response.
    """
    import inspect

    from samantha_server.api.backpressure import to_503_response

    sig = inspect.signature(to_503_response)
    param_names = list(sig.parameters.keys())

    # Only one parameter: the rejection object.
    assert param_names == ["rejection"], (
        f"to_503_response must accept only 'rejection'; got: {param_names}"
    )


def test_backpressure_rejection_str_carries_metadata() -> None:
    """str(exc) and exc.args populate via Exception.__init__ in __post_init__.

    Regression for the frozen-dataclass + Exception combo: a naive
    ``@dataclass(frozen=True) class _ (Exception)`` leaves ``exc.args == ()``
    and ``str(exc) == ""`` because the dataclass-generated ``__init__``
    never calls ``Exception.__init__``. Logging handlers that read
    ``exc.args[0]`` would raise ``IndexError``.
    """
    from samantha_server.api.backpressure import BackpressureRejection

    exc = BackpressureRejection(bound=128, depth=128, retry_after_sec=4)

    assert exc.args == (128, 128, 4)
    assert str(exc)  # non-empty


# ---------------------------------------------------------------------------
# Slice 6 — enqueue_or_reject passes event_id and otel_context through
# ---------------------------------------------------------------------------


def test_enqueue_or_reject_passes_event_id_through() -> None:
    """event_id kwarg is forwarded to queue.put; dequeued item carries it."""
    from samantha_server.api.backpressure import enqueue_or_reject

    async def run() -> None:
        q = PriorityEventQueue(maxsize=4)
        counters = CounterRegistry()

        await enqueue_or_reject(
            q,
            item="payload",
            priority=EventPriority.ROUTINE,
            counters=counters,
            retry_after_sec=5,
            event_id="my-event-id",
        )

        item = await q.get()
        assert item.event_id == "my-event-id"

    asyncio.run(run())


def test_enqueue_or_reject_passes_otel_context_through() -> None:
    """otel_context kwarg is forwarded to queue.put; dequeued item carries it.

    Closes the silent-typo gap: a wrong kwarg name at the queue.put
    call site would otherwise be invisible (anything passed via
    **kwargs would just be ignored).
    """
    from samantha_server.api.backpressure import enqueue_or_reject

    sentinel = object()

    async def run() -> None:
        q = PriorityEventQueue(maxsize=4)
        counters = CounterRegistry()

        await enqueue_or_reject(
            q,
            item="payload",
            priority=EventPriority.ROUTINE,
            counters=counters,
            retry_after_sec=5,
            otel_context=sentinel,
        )

        item = await q.get()
        assert item.otel_context is sentinel

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Slice 7 — Concurrent submission race
# ---------------------------------------------------------------------------


def test_concurrent_submissions_never_exceed_bound() -> None:
    """A burst of concurrent puts must not break the wrapper's bookkeeping.

    Plan-mandated coverage (phase-3-implementation.md § Step 3): "no
    negative depth, no off-by-one — asyncio.PriorityQueue is asyncio-safe
    but the wrapper's bookkeeping must be too."

    Submits 2 * maxsize concurrent enqueue_or_reject calls. Asserts:
      - exactly maxsize calls succeed (the queue ends at maxsize occupancy)
      - the remaining maxsize calls raise BackpressureRejection
      - queue_overflow_rejections counter equals the rejection count
      - slot_count never exceeds maxsize during the race
    """
    from samantha_server.api.backpressure import BackpressureRejection, enqueue_or_reject

    maxsize = 4
    burst = 2 * maxsize

    async def run() -> None:
        q = PriorityEventQueue(maxsize=maxsize)
        counters = CounterRegistry()

        async def submit(i: int) -> str:
            try:
                await enqueue_or_reject(
                    q,
                    item=f"event-{i}",
                    priority=EventPriority.ROUTINE,
                    counters=counters,
                    retry_after_sec=5,
                )
                return "accepted"
            except BackpressureRejection:
                return "rejected"

        results = await asyncio.gather(*[submit(i) for i in range(burst)])

        accepted = results.count("accepted")
        rejected = results.count("rejected")

        assert accepted == maxsize, f"expected {maxsize} accepted, got {accepted}"
        assert rejected == burst - maxsize, f"expected {burst - maxsize} rejected, got {rejected}"
        assert counters.queue_overflow_rejections.value == rejected
        assert q.slot_count == maxsize
        assert q.qsize() == maxsize  # _live_count, since no cancellations
        assert q.qsize() >= 0  # no negative-depth bookkeeping bug

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Slice 8 — STAT and ROUTINE share the same overflow counter
# ---------------------------------------------------------------------------


def test_stat_and_routine_share_overflow_counter() -> None:
    """Rejecting a STAT and a ROUTINE event each bumps the same counter once.

    G6 specifies STAT respects the bound; the operational rule says STAT
    503s page on-call. The shared counter is the v0 surface for "any
    503 happened" — per-priority breakdown is deferred to the dashboard
    (Step 11).
    """
    from samantha_server.api.backpressure import BackpressureRejection, enqueue_or_reject

    async def run() -> None:
        q = await _make_full_queue(maxsize=2)
        counters = CounterRegistry()

        with pytest.raises(BackpressureRejection):
            await enqueue_or_reject(
                q,
                item="routine",
                priority=EventPriority.ROUTINE,
                counters=counters,
                retry_after_sec=5,
            )

        with pytest.raises(BackpressureRejection):
            await enqueue_or_reject(
                q,
                item="stat",
                priority=EventPriority.STAT,
                counters=counters,
                retry_after_sec=5,
            )

        assert counters.queue_overflow_rejections.value == 2

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Slice 9 — PriorityEventQueue.maxsize / slot_count direct assertions
# ---------------------------------------------------------------------------


def test_priority_queue_maxsize_property_returns_configured_capacity() -> None:
    """Direct test of PriorityEventQueue.maxsize (not via rejection assertions)."""
    q = PriorityEventQueue(maxsize=42)
    assert q.maxsize == 42


def test_priority_queue_slot_count_includes_tombstones() -> None:
    """slot_count reflects heap occupancy (tombstones counted), not _live_count.

    qsize() returns _live_count (live items only); slot_count returns the
    underlying asyncio.PriorityQueue.qsize() which includes cancelled items
    that haven't been drained yet. The distinction matters for the 503
    body's queue_depth field — backpressure fires on slot occupancy.
    """

    async def run() -> None:
        q = PriorityEventQueue(maxsize=4)
        await q.put("a", EventPriority.ROUTINE, event_id="evt-a")
        await q.put("b", EventPriority.ROUTINE, event_id="evt-b")

        # Cancel one — the tombstone stays in the heap until get() drains it.
        cancelled = q.cancel("evt-a")
        assert cancelled is True

        # qsize() (live count) drops; slot_count (heap occupancy) does not.
        assert q.qsize() == 1
        assert q.slot_count == 2

    asyncio.run(run())
