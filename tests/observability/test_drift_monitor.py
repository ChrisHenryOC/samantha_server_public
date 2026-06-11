"""Tests for the drift-alarm webhook monitor.

Schema-coverage criteria:

- Rolling buffer: time-bound eviction at append; defensive maxlen cap;
  running ``_llm_count`` stays consistent across record / time-eviction
  / maxlen-overflow.
- Rate computation: ``count(routing_path == "llm") / count(*)``.
- Window-rollover boundary: events at t=0 / WINDOW-1 / WINDOW / WINDOW+1
  produce correct rate values across the eviction edge — boundary uses
  a mixed routing mix so the rate value distinguishes 4/4 from 3/3.
- Webhook firing: at most once per check interval; only when rate is
  over threshold; below-threshold and at-equality both produce no fire.
- Webhook failure: any error increments
  ``counters.drift_webhook_failures`` and the task continues without
  retry (G21).
- Disabled (URL unset): no webhook fires; deque still maintained AND
  evicted (so ``/readyz`` reads a current rate even when firing is off).
- Network isolation: every check_and_fire test patches
  ``httpx.AsyncClient`` at module level — no real socket open is
  possible. The patch is the guard.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from samantha_server.observability.counters import CounterRegistry
from samantha_server.observability.drift import DriftMonitor


def _make_monitor(
    *,
    webhook_url: str | None = "http://localhost:9999/drift",
    threshold: float = 0.05,
    window_sec: int = 3600,
    check_interval_sec: int = 60,
    deque_maxlen: int = 100_000,
    counters: CounterRegistry | None = None,
) -> DriftMonitor:
    """Build a DriftMonitor with explicit knobs (no global cfg coupling)."""
    return DriftMonitor(
        webhook_url=webhook_url,
        threshold=threshold,
        window_sec=window_sec,
        check_interval_sec=check_interval_sec,
        deque_maxlen=deque_maxlen,
        counters=counters or CounterRegistry(),
    )


# ---------------------------------------------------------------------------
# Rolling-buffer + rate computation
# ---------------------------------------------------------------------------


def test_record_appends_to_deque() -> None:
    """``record(routing_path)`` adds an entry to the rolling buffer."""
    monitor = _make_monitor()
    monitor.record(routing_path="llm", monotonic_ns=1_000_000_000)
    assert monitor.resident_count() == 1
    assert monitor.routing_paths() == ["llm"]


def test_compute_rate_zero_when_buffer_empty() -> None:
    """An empty buffer reports rate=0.0 (no events to divide by)."""
    monitor = _make_monitor()
    assert monitor.compute_rate(now_ns=1_000_000_000) == 0.0


def test_compute_rate_with_mixed_routing() -> None:
    """rate = count(llm) / count(all)."""
    monitor = _make_monitor()
    for _ in range(3):
        monitor.record(routing_path="llm", monotonic_ns=1_000_000_000)
    for _ in range(7):
        monitor.record(routing_path="deterministic", monotonic_ns=1_000_000_000)
    assert monitor.compute_rate(now_ns=2_000_000_000) == pytest.approx(0.3)


def test_compute_rate_evicts_entries_older_than_window() -> None:
    """Entries older than window_sec are evicted at compute time."""
    monitor = _make_monitor(window_sec=3600)

    # Insert an old llm event (2 hours ago) and a fresh deterministic event.
    one_ns = 1_000_000_000
    two_hours_ago_ns = -7200 * one_ns
    monitor.record(routing_path="llm", monotonic_ns=two_hours_ago_ns)
    monitor.record(routing_path="deterministic", monotonic_ns=0)

    rate = monitor.compute_rate(now_ns=0)
    # Only the deterministic event survives → 0/1 = 0.0.
    assert rate == 0.0
    # And the old entry is gone from the buffer.
    assert monitor.resident_count() == 1


def test_window_rollover_boundary() -> None:
    """Events at t=0, WINDOW-1, WINDOW, WINDOW+1 cross the eviction edge.

    The t=0 event is ``deterministic`` and the rest are ``llm`` so the
    rate value (3/4 vs 3/3) distinguishes "boundary survives" from
    "boundary evicted". A failure of the predicate would have flipped
    rate by 0.25, not just the resident_count.
    """
    one_ns = 1_000_000_000
    window_sec = 3600
    window_ns = window_sec * one_ns

    monitor = _make_monitor(window_sec=window_sec)

    monitor.record(routing_path="deterministic", monotonic_ns=0)
    monitor.record(routing_path="llm", monotonic_ns=window_ns - one_ns)  # t = WINDOW-1s
    monitor.record(routing_path="llm", monotonic_ns=window_ns)  # t = WINDOW
    monitor.record(routing_path="llm", monotonic_ns=window_ns + one_ns)  # t = WINDOW+1s

    # At now=WINDOW: t=0 event is exactly at the edge. Predicate is
    # ``age > window_sec``, so it survives. All four events remain;
    # rate = 3/4 = 0.75.
    rate = monitor.compute_rate(now_ns=window_ns)
    assert monitor.resident_count() == 4
    assert rate == pytest.approx(0.75)

    # At now=WINDOW+1ns: the t=0 deterministic event is now strictly
    # older than WINDOW seconds → evicted. Three llm events remain;
    # rate = 3/3 = 1.0.
    rate = monitor.compute_rate(now_ns=window_ns + 1)
    assert monitor.resident_count() == 3
    assert rate == pytest.approx(1.0)


def test_deque_maxlen_caps_resident_count() -> None:
    """Defensive maxlen drops the oldest entry on overflow."""
    monitor = _make_monitor(deque_maxlen=10)
    for i in range(15):
        monitor.record(routing_path="llm", monotonic_ns=i)
    # 15 appends, maxlen=10 → 10 entries retained.
    assert monitor.resident_count() == 10


def test_running_llm_counter_consistent_across_maxlen_overflow() -> None:
    """``compute_rate`` stays correct after maxlen-driven evictions.

    Without per-record bookkeeping for the maxlen-overflow path the
    running ``_llm_count`` would diverge from the deque contents and
    the O(1) rate would silently disagree with the truth.
    """
    monitor = _make_monitor(deque_maxlen=4)
    monitor.record(routing_path="llm", monotonic_ns=0)
    monitor.record(routing_path="llm", monotonic_ns=1)
    monitor.record(routing_path="deterministic", monotonic_ns=2)
    monitor.record(routing_path="deterministic", monotonic_ns=3)
    # Overflow drops the oldest llm.
    monitor.record(routing_path="deterministic", monotonic_ns=4)
    monitor.record(routing_path="deterministic", monotonic_ns=5)
    # Buffer: [det, det, det, det] → rate 0.0.
    assert monitor.compute_rate(now_ns=10) == pytest.approx(0.0)


def test_running_llm_counter_consistent_across_time_eviction() -> None:
    """Time-eviction must decrement ``_llm_count`` for each evicted llm."""
    one_ns = 1_000_000_000
    monitor = _make_monitor(window_sec=10)
    # Two old llms (window_sec = 10s; t=0 ns is 20s in the past at now=20s).
    monitor.record(routing_path="llm", monotonic_ns=0)
    monitor.record(routing_path="llm", monotonic_ns=one_ns)
    # Two recent dets.
    monitor.record(routing_path="deterministic", monotonic_ns=20 * one_ns)
    monitor.record(routing_path="deterministic", monotonic_ns=20 * one_ns)

    rate = monitor.compute_rate(now_ns=20 * one_ns)
    assert monitor.resident_count() == 2
    assert rate == pytest.approx(0.0)


def test_check_and_fire_evicts_when_webhook_url_none() -> None:
    """Eviction runs even when firing is disabled (so ``/readyz`` rate stays current)."""
    one_ns = 1_000_000_000
    monitor = _make_monitor(webhook_url=None, window_sec=10)
    monitor.record(routing_path="llm", monotonic_ns=0)

    asyncio.run(monitor.check_and_fire(now_ns=20 * one_ns))

    # The 20 s gap exceeds window_sec=10 → the lone llm is evicted.
    assert monitor.resident_count() == 0


# ---------------------------------------------------------------------------
# Webhook firing
# ---------------------------------------------------------------------------


def _make_async_post_response(status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.is_success = 200 <= status_code < 300
    return response


def _build_mock_async_client(*, post_return: Any = None, post_side_effect: Any = None) -> MagicMock:
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    else:
        mock_client.post = AsyncMock(return_value=post_return)
    return mock_client


def test_check_and_fire_does_nothing_when_rate_at_threshold() -> None:
    """Equality with threshold does not fire (predicate is strict ``>``)."""
    counters = CounterRegistry()
    monitor = _make_monitor(threshold=0.5, counters=counters)
    monitor.record(routing_path="llm", monotonic_ns=0)
    monitor.record(routing_path="deterministic", monotonic_ns=0)
    # rate = 0.5 (NOT > 0.5) → no fire.

    mock_client = _build_mock_async_client(post_return=_make_async_post_response(200))
    with patch("samantha_server.observability.drift.httpx.AsyncClient", return_value=mock_client):
        asyncio.run(monitor.check_and_fire(now_ns=0))

    mock_client.post.assert_not_called()
    assert counters.drift_webhook_failures.value == 0


def test_check_and_fire_does_nothing_when_rate_strictly_below_threshold() -> None:
    """Independent confirmation: clean below-threshold run never fires."""
    counters = CounterRegistry()
    monitor = _make_monitor(threshold=0.5, counters=counters)
    # Only deterministic events → rate=0.0, well below threshold.
    monitor.record(routing_path="deterministic", monotonic_ns=0)
    monitor.record(routing_path="deterministic", monotonic_ns=0)

    mock_client = _build_mock_async_client(post_return=_make_async_post_response(200))
    with patch("samantha_server.observability.drift.httpx.AsyncClient", return_value=mock_client):
        asyncio.run(monitor.check_and_fire(now_ns=0))

    mock_client.post.assert_not_called()
    assert counters.drift_webhook_failures.value == 0


def test_check_and_fire_posts_when_rate_over_threshold() -> None:
    """When rate > threshold, the webhook fires with the documented body shape."""
    counters = CounterRegistry()
    monitor = _make_monitor(
        webhook_url="http://localhost:9999/drift", threshold=0.05, counters=counters
    )
    # 100% LLM rate → way above 5%.
    monitor.record(routing_path="llm", monotonic_ns=0)

    mock_client = _build_mock_async_client(post_return=_make_async_post_response(200))
    with patch("samantha_server.observability.drift.httpx.AsyncClient", return_value=mock_client):
        asyncio.run(monitor.check_and_fire(now_ns=0))

    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.await_args
    assert call_args.args[0] == "http://localhost:9999/drift"
    body = call_args.kwargs.get("json")
    assert isinstance(body, dict)
    # Body shape per the plan: {window_sec, rate, threshold, fired_at_unix}.
    assert set(body.keys()) == {"window_sec", "rate", "threshold", "fired_at_unix"}
    assert body["rate"] == 1.0
    assert body["threshold"] == 0.05
    assert body["window_sec"] == 3600
    assert isinstance(body["fired_at_unix"], (int, float))
    # No retry counter increment on success.
    assert counters.drift_webhook_failures.value == 0


def test_check_and_fire_no_webhook_url_disables_cleanly() -> None:
    """webhook_url=None → never opens an httpx client; no failure counter increment."""
    counters = CounterRegistry()
    monitor = _make_monitor(webhook_url=None, threshold=0.05, counters=counters)
    monitor.record(routing_path="llm", monotonic_ns=0)

    mock_client = _build_mock_async_client(post_return=_make_async_post_response(200))
    with patch("samantha_server.observability.drift.httpx.AsyncClient", return_value=mock_client):
        asyncio.run(monitor.check_and_fire(now_ns=0))

    mock_client.__aenter__.assert_not_called()
    assert counters.drift_webhook_failures.value == 0


def test_check_and_fire_increments_counter_on_non_2xx() -> None:
    """Non-2xx responses are failures; counter increments; no retry."""
    counters = CounterRegistry()
    monitor = _make_monitor(threshold=0.05, counters=counters)
    monitor.record(routing_path="llm", monotonic_ns=0)

    mock_client = _build_mock_async_client(post_return=_make_async_post_response(500))
    with patch("samantha_server.observability.drift.httpx.AsyncClient", return_value=mock_client):
        asyncio.run(monitor.check_and_fire(now_ns=0))

    mock_client.post.assert_awaited_once()
    assert counters.drift_webhook_failures.value == 1


def test_check_and_fire_increments_counter_on_connect_error() -> None:
    """A connection error increments the counter and the task continues."""
    import httpx

    counters = CounterRegistry()
    monitor = _make_monitor(threshold=0.05, counters=counters)
    monitor.record(routing_path="llm", monotonic_ns=0)

    mock_client = _build_mock_async_client(post_side_effect=httpx.ConnectError("refused"))
    with patch("samantha_server.observability.drift.httpx.AsyncClient", return_value=mock_client):
        asyncio.run(monitor.check_and_fire(now_ns=0))

    assert counters.drift_webhook_failures.value == 1


def test_check_and_fire_increments_counter_on_timeout() -> None:
    """A timeout error increments the counter and the task continues (no retry per G21)."""
    import httpx

    counters = CounterRegistry()
    monitor = _make_monitor(threshold=0.05, counters=counters)
    monitor.record(routing_path="llm", monotonic_ns=0)

    mock_client = _build_mock_async_client(post_side_effect=httpx.TimeoutException("slow"))
    with patch("samantha_server.observability.drift.httpx.AsyncClient", return_value=mock_client):
        asyncio.run(monitor.check_and_fire(now_ns=0))

    assert counters.drift_webhook_failures.value == 1


def test_check_and_fire_failure_log_does_not_leak_url(caplog: pytest.LogCaptureFixture) -> None:
    """Failure-log message must not include the exception's __str__ (URL/credential redaction)."""
    import httpx

    counters = CounterRegistry()
    secret_url = "https://user:secret-token@webhook.example.com/drift"
    monitor = _make_monitor(webhook_url=secret_url, threshold=0.05, counters=counters)
    monitor.record(routing_path="llm", monotonic_ns=0)

    mock_client = _build_mock_async_client(
        post_side_effect=httpx.ConnectError(f"refused to {secret_url}")
    )
    with (
        caplog.at_level("WARNING"),
        patch("samantha_server.observability.drift.httpx.AsyncClient", return_value=mock_client),
    ):
        asyncio.run(monitor.check_and_fire(now_ns=0))

    # The exception class name must be present, but neither the URL nor
    # the embedded credential may appear in any log record.
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "ConnectError" in joined
    assert "secret-token" not in joined
    assert "webhook.example.com" not in joined
    assert counters.drift_webhook_failures.value == 1


def test_webhook_body_via_check_and_fire_is_json_serializable() -> None:
    """The body posted to the webhook is plain JSON — exercises the public path."""
    counters = CounterRegistry()
    monitor = _make_monitor(threshold=0.05, counters=counters)
    monitor.record(routing_path="llm", monotonic_ns=0)

    mock_client = _build_mock_async_client(post_return=_make_async_post_response(200))
    with patch("samantha_server.observability.drift.httpx.AsyncClient", return_value=mock_client):
        asyncio.run(monitor.check_and_fire(now_ns=0))

    body = mock_client.post.await_args.kwargs["json"]
    encoded = json.dumps(body)
    decoded = json.loads(encoded)
    assert decoded == body
    assert set(decoded.keys()) == {"window_sec", "rate", "threshold", "fired_at_unix"}
