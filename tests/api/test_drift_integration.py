"""Integration tests for the drift-alarm wiring.

Verifies that:

- The consumer ``_consume`` calls ``drift_monitor.record(...)`` after a
  successful dispatch, sourcing ``routing_path`` from the
  ``EventDispatchContext`` (orchestrator-owned), never from the
  ``EngineDecision``.
- The recording happens AFTER ``set_result`` so an observability bug
  cannot mis-resolve the caller's future as a dispatch failure.
- The lifespan's ``_drift_loop`` driver re-raises ``CancelledError``,
  swallows other exceptions (incrementing ``drift_loop_errors``), and
  drives ``check_and_fire`` on each iteration.
- ``AppState.aclose()`` cancels and awaits the drift task on shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from samantha_server.api.events import _QueuePayload
from samantha_server.observability.counters import CounterRegistry
from samantha_server.observability.drift import DriftMonitor
from samantha_server.queue.priority import EventPriority
from tests.api.helpers import (
    _make_mock_llm_client,
    make_clinical_query_ctx_dict,
    make_consume_app_state,
)


def _build_drift_monitor(
    counters: CounterRegistry,
    *,
    webhook_url: str | None = None,
    check_interval_sec: int = 60,
) -> DriftMonitor:
    """Build a DriftMonitor sharing the supplied counters."""
    return DriftMonitor(
        webhook_url=webhook_url,
        threshold=0.05,
        window_sec=3600,
        check_interval_sec=check_interval_sec,
        deque_maxlen=10_000,
        counters=counters,
    )


def _build_state_with_drift(*, check_interval_sec: int = 60) -> Any:
    """Build an AppState with a DriftMonitor wired in at construction time."""
    counters = CounterRegistry()
    monitor = _build_drift_monitor(counters, check_interval_sec=check_interval_sec)
    state = make_consume_app_state(_make_mock_llm_client(), drift_monitor=monitor)
    # Bind the same counter registry into the monitor so tests asserting on
    # ``state.counters.drift_*`` see the effects of monitor side-effects.
    monitor._counters = state.counters
    return state


def test_consume_records_dispatch_via_public_accessor() -> None:
    """Consumer records the dispatch_ctx routing_path; verified via routing_paths()."""
    from samantha_server.api.app import _consume

    async def run() -> None:
        state = _build_state_with_drift()

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        payload = _QueuePayload(
            future=future,
            ctx_dict=make_clinical_query_ctx_dict(order_id="DRIFT-001"),
            session_id="drift-test-1",
            priority=EventPriority.ROUTINE,
        )

        await state.queue.put(payload, EventPriority.ROUTINE)
        task = asyncio.create_task(_consume(state))

        _, _, dispatch_ctx = await asyncio.wait_for(future, timeout=5.0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert state.drift_monitor.resident_count() == 1
        # Public accessor — no reach into ``_buffer``.
        assert state.drift_monitor.routing_paths() == [dispatch_ctx.routing_path]

    asyncio.run(run())


def test_consume_records_after_set_result() -> None:
    """``set_result`` resolves before ``record`` runs.

    Pinned by wrapping ``record`` with a probe that captures
    ``future.done()`` at the moment record is invoked. If record were
    moved before set_result, the probe would observe ``done() == False``.
    """
    from samantha_server.api.app import _consume

    async def run() -> None:
        state = _build_state_with_drift()

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()

        observed_done_at_record: list[bool] = []
        original_record = state.drift_monitor.record

        def _record_probe(**kwargs: Any) -> None:
            observed_done_at_record.append(future.done())
            original_record(**kwargs)

        state.drift_monitor.record = _record_probe  # type: ignore[method-assign]

        payload = _QueuePayload(
            future=future,
            ctx_dict=make_clinical_query_ctx_dict(order_id="DRIFT-ORD"),
            session_id="drift-test-order",
            priority=EventPriority.ROUTINE,
        )

        await state.queue.put(payload, EventPriority.ROUTINE)
        task = asyncio.create_task(_consume(state))
        await asyncio.wait_for(future, timeout=5.0)
        # Let the consumer reach the record() call.
        await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # record() ran AFTER set_result, so future.done() was True at
        # the moment record was invoked.
        assert observed_done_at_record == [True]

    asyncio.run(run())


def test_consume_swallows_record_failure_without_failing_dispatch() -> None:
    """If ``record`` raises, the dispatch's future still resolves successfully."""
    from samantha_server.api.app import _consume

    async def run() -> None:
        state = _build_state_with_drift()
        # Replace ``record`` with a raising stub. The consumer's narrow
        # try/except must catch it and let set_result stand.
        state.drift_monitor.record = lambda **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
            RuntimeError("simulated drift bug")
        )

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        payload = _QueuePayload(
            future=future,
            ctx_dict=make_clinical_query_ctx_dict(order_id="DRIFT-ROBUST"),
            session_id="drift-robust",
            priority=EventPriority.ROUTINE,
        )

        await state.queue.put(payload, EventPriority.ROUTINE)
        task = asyncio.create_task(_consume(state))

        # The future MUST resolve successfully (no set_exception).
        receipt, decision, dispatch_ctx = await asyncio.wait_for(future, timeout=5.0)
        assert receipt is not None
        assert decision is not None
        assert dispatch_ctx is not None

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_consume_does_not_record_when_drift_monitor_absent() -> None:
    """If drift_monitor is None, the consumer dispatches without raising."""
    from samantha_server.api.app import _consume

    async def run() -> None:
        state = make_consume_app_state(_make_mock_llm_client())
        assert state.drift_monitor is None

        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        payload = _QueuePayload(
            future=future,
            ctx_dict=make_clinical_query_ctx_dict(order_id="DRIFT-002"),
            session_id="drift-test-2",
            priority=EventPriority.ROUTINE,
        )

        await state.queue.put(payload, EventPriority.ROUTINE)
        task = asyncio.create_task(_consume(state))
        await asyncio.wait_for(future, timeout=5.0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())


# ---------------------------------------------------------------------------
# _drift_loop driver
# ---------------------------------------------------------------------------


def test_drift_loop_calls_check_and_fire_on_each_tick() -> None:
    """The loop sleeps then calls check_and_fire — exercises the production driver path."""
    from samantha_server.api.app import _drift_loop

    async def run() -> None:
        # Use a 0-second interval so the loop ticks immediately.
        state = _build_state_with_drift(check_interval_sec=0)
        state.drift_monitor.check_and_fire = AsyncMock()  # type: ignore[method-assign]

        task = asyncio.create_task(_drift_loop(state))
        # Yield long enough for at least two iterations.
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert state.drift_monitor.check_and_fire.await_count >= 1

    asyncio.run(run())


def test_drift_loop_continues_and_increments_counter_on_exception() -> None:
    """check_and_fire exception → drift_loop_errors increments; loop continues."""
    from samantha_server.api.app import _drift_loop

    async def run() -> None:
        state = _build_state_with_drift(check_interval_sec=0)

        call_count = 0

        async def _failing_check(**_kwargs: Any) -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated check_and_fire bug")

        state.drift_monitor.check_and_fire = _failing_check  # type: ignore[method-assign]

        task = asyncio.create_task(_drift_loop(state))
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # Loop continued past the first failure → counter incremented at least twice
        # (once per failed iteration).
        assert call_count >= 2
        assert state.counters.drift_loop_errors.value >= 2

    asyncio.run(run())


def test_drift_loop_reraises_cancelled_error() -> None:
    """``CancelledError`` exits the loop instead of being swallowed."""
    from samantha_server.api.app import _drift_loop

    async def run() -> None:
        state = _build_state_with_drift(check_interval_sec=60)
        state.drift_monitor.check_and_fire = AsyncMock()  # type: ignore[method-assign]

        task = asyncio.create_task(_drift_loop(state))
        await asyncio.sleep(0)  # Let the task start
        task.cancel()
        # The cancellation must propagate as CancelledError, not be swallowed.
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


# ---------------------------------------------------------------------------
# aclose() handling of the drift task
# ---------------------------------------------------------------------------


def test_aclose_cancels_drift_task_cleanly() -> None:
    """``AppState.aclose()`` cancels and awaits the drift task."""

    async def run() -> None:
        state = _build_state_with_drift()

        async def _idle() -> None:
            await asyncio.sleep(3600)

        state.drift_task = asyncio.create_task(_idle())

        await state.aclose()

        assert state.drift_task.cancelled() or state.drift_task.done()

    asyncio.run(run())


def test_aclose_logs_failed_drift_task_without_raising() -> None:
    """A drift task that died before shutdown is logged in aclose, not re-raised."""

    async def run() -> None:
        state = _build_state_with_drift()

        async def _boom() -> None:
            raise RuntimeError("simulated drift task failure")

        state.drift_task = asyncio.create_task(_boom())
        with contextlib.suppress(RuntimeError):
            await state.drift_task

        # aclose() should not raise — the failure is logged.
        await state.aclose()

    asyncio.run(run())


def test_aclose_no_drift_task_no_op() -> None:
    """aclose() with drift_task=None does not raise."""

    async def run() -> None:
        state = make_consume_app_state(_make_mock_llm_client())
        assert state.drift_task is None
        await state.aclose()

    asyncio.run(run())


def test_drift_monitor_default_is_none() -> None:
    """AppState.drift_monitor defaults to None — existing test helpers stay valid."""
    from tests.api.helpers import make_minimal_app_state

    state = make_minimal_app_state()
    assert state.drift_monitor is None
    assert state.drift_task is None
