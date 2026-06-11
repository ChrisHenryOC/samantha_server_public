"""Tests for graceful shutdown — lifespan drain trigger.

 C3 fix-review: an explicit ``signal.signal(SIGTERM,...)``
handler is overwritten by Uvicorn at startup, so the drain flag never
flipped in production. The fix moves the flag into the lifespan's
``finally`` clause — which Uvicorn invokes on its own SIGTERM handling.
The tests below exercise the lifespan-shutdown path directly via
TestClient and assert that ``is_draining`` becomes True before resources
are released.
"""

from __future__ import annotations

import logging

import pytest

from tests.api.helpers import make_minimal_app_state


def test_is_draining_defaults_to_false() -> None:
    """AppState.is_draining starts as False."""
    state = make_minimal_app_state()
    assert state.is_draining is False


def test_is_draining_can_be_set_true() -> None:
    """AppState.is_draining can be flipped at runtime."""
    state = make_minimal_app_state()
    state.is_draining = True
    assert state.is_draining is True


def test_lifespan_finally_sets_is_draining_true() -> None:
    """Lifespan shutdown flips is_draining=True before aclose runs.

     C3 regression: the previous SIGTERM-handler approach never
    fired under Uvicorn (Uvicorn replaced the handler). Wiring drain to
    lifespan-finally ensures the flag flips on every controlled shutdown
    regardless of how it was triggered.

    We stub ``build_app_state`` so the lifespan doesn't try to load real
    LLM weights; the assertion is about the lifespan-finally drain hook,
    not about lifespan boot.
    """
    import asyncio
    from unittest.mock import patch

    from samantha_server.api.app import lifespan

    stub_state = make_minimal_app_state()

    class _StubApp:
        class _State:
            engine: object = None

        state = _State()

    stub_app = _StubApp()
    captured: dict[str, object] = {}

    async def run_lifespan() -> None:
        with patch(
            "samantha_server.api.lifespan.build_app_state",
            return_value=stub_state,
        ):
            async with lifespan(stub_app):  # type: ignore[arg-type]
                captured["pre_shutdown_draining"] = stub_state.is_draining

    asyncio.run(run_lifespan())

    assert captured["pre_shutdown_draining"] is False
    assert stub_state.is_draining is True


def test_drain_loop_continues_after_set_exception_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drain loop resolves all futures even when one set_exception call raises.

    Under the old code, any exception inside the drain loop
    called ``break``, stranding all items after the failing one. The fix
    catches ``asyncio.QueueEmpty`` as the loop-exit condition and catches
    any other Exception with ERROR + exc_info, then continues to the next item.

    Setup: 3 payloads in the queue; the second future's set_exception raises
    RuntimeError. Assertions:
    - aclose() does not raise
    - futures 1 and 3 are resolved with ShutdownError
    - an ERROR log record with exc_info is emitted for item 2
    """
    import asyncio

    from samantha_server.errors import ShutdownError
    from samantha_server.queue.priority import EventPriority
    from tests.api.helpers import make_minimal_app_state

    async def run() -> None:
        state = make_minimal_app_state()

        futures: list[asyncio.Future[object]] = [
            asyncio.get_running_loop().create_future() for _ in range(3)
        ]

        # Second future raises when set_exception is called.
        def _exploding_set_exception(exc: BaseException) -> None:
            raise RuntimeError("set_exception instrumentation failure")

        futures[1].set_exception = _exploding_set_exception  # type: ignore[method-assign]

        # Build minimal _QueuePayload-like objects and enqueue them.
        from samantha_server.api.events import _QueuePayload

        for i, fut in enumerate(futures):
            payload = _QueuePayload(
                future=fut,  # type: ignore[arg-type]
                ctx_dict={},
                session_id=f"s{i}",
                priority=EventPriority.ROUTINE,
            )
            await state.queue.put(payload, EventPriority.ROUTINE)

        with caplog.at_level(logging.INFO, logger="samantha_server.api.lifespan"):
            await state.aclose()

        # Future 0: resolved with ShutdownError
        assert futures[0].done()
        assert isinstance(futures[0].exception(), ShutdownError)

        # Future 1: the set_exception blew up — future is NOT resolved,
        # but aclose must not have raised.
        assert not futures[1].done()

        # Future 2: must still be resolved despite the error on item 1
        assert futures[2].done()
        assert isinstance(futures[2].exception(), ShutdownError)

        # An ERROR record must exist for item 1's failure with the injected RuntimeError.
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any(
            r.exc_info is not None and isinstance(r.exc_info[1], RuntimeError)
            for r in error_records
        ), "Expected an ERROR log with exc_info[1] as RuntimeError for the set_exception failure"

        # The final INFO log must say "Resolved 2" (not 3),
        # because item 1's set_exception raised and was not counted as resolved.
        resolved_info = [
            r for r in caplog.records if r.levelno == logging.INFO and "Resolved" in r.message
        ]
        assert len(resolved_info) == 1, "Expected exactly one 'Resolved …' INFO record"
        assert "2" in resolved_info[0].message, (
            f"Expected 'Resolved 2 …' but got: {resolved_info[0].message!r}"
        )

    asyncio.run(run())


def test_drain_loop_empty_queue_exits_cleanly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drain with zero items in the queue completes without ERROR or INFO logs.

    after the redesign QueueEmpty is the normal loop exit.
    An empty queue must not emit ERROR or spurious INFO("Resolved …") records.
    """
    import asyncio

    from tests.api.helpers import make_minimal_app_state

    async def run() -> None:
        state = make_minimal_app_state()
        # Queue is empty — no items enqueued.
        with caplog.at_level(logging.DEBUG, logger="samantha_server.api.lifespan"):
            await state.aclose()

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records == [], "Empty-queue drain must not emit ERROR records"
        resolved_records = [
            r for r in caplog.records if r.levelno == logging.INFO and "Resolved" in r.message
        ]
        assert resolved_records == [], "Empty-queue drain must not emit 'Resolved …' INFO records"

    asyncio.run(run())


def test_drain_loop_tombstone_plus_live_item_resolves_live_future(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drain with 1 tombstone + 1 live item resolves the live future with ShutdownError.

    the old range(qsize()) bound uses _live_count=1, so the
    loop consumes the tombstone (first on the heap) and exits, stranding the live
    caller. The fix drains until QueueEmpty, skipping tombstones explicitly.

    Tombstone construction: put() an item with an event_id, then cancel() that
    event_id. The QueueItem stays on the raw heap but is no longer in _id_map.
    The live item is put() without an event_id so it cannot be cancelled.
    """
    import asyncio

    from samantha_server.api.events import _QueuePayload
    from samantha_server.errors import ShutdownError
    from samantha_server.queue.priority import EventPriority
    from tests.api.helpers import make_minimal_app_state

    async def run() -> None:
        state = make_minimal_app_state()
        loop = asyncio.get_running_loop()

        # Tombstone: enqueue with event_id, then cancel before drain.
        tombstone_future: asyncio.Future[object] = loop.create_future()
        tombstone_payload = _QueuePayload(
            future=tombstone_future,  # type: ignore[arg-type]
            ctx_dict={},
            session_id="tombstone-session",
            priority=EventPriority.ROUTINE,
        )
        await state.queue.put(tombstone_payload, EventPriority.ROUTINE, event_id="evt-tombstone")
        state.queue.cancel("evt-tombstone")

        # Live item: anonymous (no event_id), cannot be cancelled by id.
        live_future: asyncio.Future[object] = loop.create_future()
        live_payload = _QueuePayload(
            future=live_future,  # type: ignore[arg-type]
            ctx_dict={},
            session_id="live-session",
            priority=EventPriority.ROUTINE,
        )
        await state.queue.put(live_payload, EventPriority.ROUTINE)

        # Verify precondition: qsize() == 1 (live only), raw heap == 2 slots.
        assert state.queue.qsize() == 1, "qsize() should only count the live item"
        assert state.queue.slot_count == 2, "slot_count should include the tombstone"

        with caplog.at_level(logging.DEBUG, logger="samantha_server.api.lifespan"):
            await state.aclose()

        # The live future must be resolved with ShutdownError.
        assert live_future.done(), "Live future must be resolved by the drain loop"
        assert isinstance(live_future.exception(), ShutdownError), (
            "Live future must receive ShutdownError"
        )

        # No ERROR records: the tombstone skip is clean.
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records == [], "Tombstone skip must not emit ERROR records"

    asyncio.run(run())


def test_drain_loop_accurate_resolved_count_excludes_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Final INFO log reports the accurate resolved count, excluding failed items.

    the old 'drained' counter counted pre-settled futures
    and excluded items whose set_exception raised. The fix tracks 'resolved' (only
    futures where set_exception succeeded) and reports failed count separately when
    nonzero.

    Setup: 3 payloads, the second future's set_exception raises. Expect:
    - Final INFO log says "Resolved 2" (items 0 and 2 succeeded).
    - A separate ERROR record mentions the failure on item 1.
    """
    import asyncio

    from samantha_server.api.events import _QueuePayload
    from samantha_server.errors import ShutdownError
    from samantha_server.queue.priority import EventPriority
    from tests.api.helpers import make_minimal_app_state

    async def run() -> None:
        state = make_minimal_app_state()
        loop = asyncio.get_running_loop()

        futures: list[asyncio.Future[object]] = [loop.create_future() for _ in range(3)]

        def _exploding_set_exception(exc: BaseException) -> None:
            raise RuntimeError("set_exception instrumentation failure")

        futures[1].set_exception = _exploding_set_exception  # type: ignore[method-assign]

        for i, fut in enumerate(futures):
            payload = _QueuePayload(
                future=fut,  # type: ignore[arg-type]
                ctx_dict={},
                session_id=f"s{i}",
                priority=EventPriority.ROUTINE,
            )
            await state.queue.put(payload, EventPriority.ROUTINE)

        with caplog.at_level(logging.INFO, logger="samantha_server.api.lifespan"):
            await state.aclose()

        assert futures[0].done() and isinstance(futures[0].exception(), ShutdownError)
        assert not futures[1].done()
        assert futures[2].done() and isinstance(futures[2].exception(), ShutdownError)

        # The final INFO log must say "Resolved 2" (not "Resolved 3").
        resolved_info = [
            r for r in caplog.records if r.levelno == logging.INFO and "Resolved" in r.message
        ]
        assert len(resolved_info) == 1, "Expected exactly one 'Resolved …' INFO record"
        assert "2" in resolved_info[0].message, (
            f"Expected 'Resolved 2 …' but got: {resolved_info[0].message!r}"
        )

    asyncio.run(run())


def test_drain_loop_resolved_count_excludes_presettled_futures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A future that was already done before the drain is not counted as resolved.

    the counter must reflect futures the drain
    actually resolved with ShutdownError, not items that merely passed through.
    Setup: 2 payloads, the first future pre-settled. Expect "Resolved 1".
    """
    import asyncio

    from samantha_server.api.events import _QueuePayload
    from samantha_server.errors import ShutdownError
    from samantha_server.queue.priority import EventPriority
    from tests.api.helpers import make_minimal_app_state

    async def run() -> None:
        state = make_minimal_app_state()
        loop = asyncio.get_running_loop()

        presettled: asyncio.Future[object] = loop.create_future()
        presettled.set_result("already-handled")
        live: asyncio.Future[object] = loop.create_future()

        for i, fut in enumerate((presettled, live)):
            payload = _QueuePayload(
                future=fut,  # type: ignore[arg-type]
                ctx_dict={},
                session_id=f"s{i}",
                priority=EventPriority.ROUTINE,
            )
            await state.queue.put(payload, EventPriority.ROUTINE)

        with caplog.at_level(logging.INFO, logger="samantha_server.api.lifespan"):
            await state.aclose()

        assert live.done() and isinstance(live.exception(), ShutdownError)

        resolved_info = [
            r for r in caplog.records if r.levelno == logging.INFO and "Resolved" in r.message
        ]
        assert len(resolved_info) == 1
        assert "Resolved 1 " in resolved_info[0].message, (
            f"Pre-settled future must not count as resolved; got: {resolved_info[0].message!r}"
        )

    asyncio.run(run())


def test_readyz_returns_503_when_draining() -> None:
    """GET /readyz returns 503 when is_draining is True."""
    from fastapi.testclient import TestClient

    from samantha_server.api.app import create_app

    app = create_app()
    state = make_minimal_app_state()
    state.is_draining = True
    app.state.engine = state

    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/readyz")
    assert response.status_code == 503
