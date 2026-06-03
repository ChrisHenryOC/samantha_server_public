"""Drift-alarm webhook monitor (GH-127 Step 12).

A small periodic task computes ``llm_call_rate`` over a rolling window
from an in-memory rolling buffer owned by the orchestrator — *not*
from the Langfuse trace store. Decoupling the alarm from
``LANGFUSE_ENABLED`` keeps it available in CI and air-gapped lab runs
and makes the EU AI Act post-market monitoring story self-contained.

Design highlights
=================

- **Rolling buffer**: a single ``collections.deque`` of
  ``(monotonic_ns, routing_path)`` with two bounds applied together:
  time-bound eviction at compute time (``DRIFT_ALARM_WINDOW_SEC``) and
  a defensive count cap via ``maxlen=DRIFT_DEQUE_MAXLEN``. Per-priority
  bucketing was considered and dropped — the rate computation uses the
  combined buffer; per-priority breakdowns belong on the dashboard.
- **Running LLM counter**: ``self._llm_count`` is maintained
  incrementally on every ``record()`` and every eviction (popleft and
  maxlen-overflow), so ``compute_rate()`` is O(1). Without this, a
  100k-entry deque scanned every check_interval_sec at a tightened
  interval would take ~3-5 ms each tick.
- **No retry on webhook failure** (G21). Timeout, connection refused,
  non-2xx response, TLS error → increment
  ``counters.drift_webhook_failures`` and continue. The next interval's
  rate computation will fire again if drift persists; a sustained
  misconfigured target should not hold the drift task in retry loops.
- **5 s connect+read timeout** on the httpx client. TLS verification
  is on by default.

PHI boundary
============

The webhook body is exactly four keys: ``window_sec``, ``rate``,
``threshold``, ``fired_at_unix``. No event-type breakdown, no
session_id, no order data — drift is a notification, the dashboard
owns breakdowns.

Decision-path purity
====================

This module lives under ``observability/`` and consumes only
primitive types (str / float / int) plus ``CounterRegistry``.
``engine/`` / ``rules/`` / ``primitives/`` / ``models/`` / ``queue/``
must not import from here.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Final, Literal, TypedDict

import httpx

if TYPE_CHECKING:
    from samantha_server.observability.counters import CounterRegistry

_log = logging.getLogger(__name__)

# Connect+read timeout on the webhook httpx client. Per G21.
_HTTPX_TIMEOUT_SEC: Final[float] = 5.0

# Local mirror of ``samantha_server.api.routing.RoutingPath``. Defined
# here (rather than imported) because ``observability/`` cannot depend
# on ``api/`` without inducing a circular import — ``api/lifespan.py``
# already imports ``observability/drift.py``. Mypy still enforces the
# Literal at the call site.
RoutingPath = Literal["deterministic", "llm"]


class DriftPayload(TypedDict):
    """Locked four-key webhook body shape.

    See ``docs/observability/drift-alarm.md`` for the contract. Keys
    must not be added, removed, or renamed without a doc update and a
    receiver-side coordination — no PHI, no event-type breakdowns.
    """

    window_sec: int
    rate: float
    threshold: float
    fired_at_unix: float


class DriftMonitor:
    """In-memory rolling-window LLM-call-rate monitor + webhook fire-and-forget.

    Construction does not touch the network. Call ``record(...)`` on
    every dispatched decision; the orchestrator's lifespan starts an
    asyncio task that calls ``check_and_fire(...)`` once per
    ``check_interval_sec``.

    Disabled via ``webhook_url=None``: the buffer still records and
    eviction still runs (so ``/readyz`` could surface the current rate
    via degraded JSON) but no webhook ever fires.
    """

    def __init__(
        self,
        *,
        webhook_url: str | None,
        threshold: float,
        window_sec: int,
        check_interval_sec: int,
        deque_maxlen: int,
        counters: CounterRegistry,
    ) -> None:
        self._webhook_url = webhook_url
        self._threshold = threshold
        self._window_sec = window_sec
        self._check_interval_sec = check_interval_sec
        self._counters = counters

        # Single combined deque. Per-priority bucketing was considered
        # and dropped — the alarm fires on combined rate; per-priority
        # breakdowns are dashboard concerns.
        self._buffer: deque[tuple[int, RoutingPath]] = deque(maxlen=deque_maxlen)
        # Running count of routing_path == "llm" entries currently in
        # the buffer. Maintained on append (record), popleft (eviction),
        # and maxlen-overflow (the deque drops its left element).
        self._llm_count: int = 0

    # -----------------------------------------------------------------
    # Recording + rate computation
    # -----------------------------------------------------------------

    def record(self, *, routing_path: RoutingPath, monotonic_ns: int) -> None:
        """Append one decision to the rolling buffer.

        ``routing_path`` must be the value from
        ``EventDispatchContext.routing_path`` (orchestrator-owned),
        never derived from ``EngineDecision``.
        """
        # The deque's maxlen cap is the only path that can drop an
        # entry without going through ``_evict_older_than_window``.
        # Mirror the bookkeeping here so the running counter stays
        # consistent across overflow. ``maxlen`` is always a positive
        # int (enforced by config validation), so the ``is not None``
        # guard is omitted.
        if len(self._buffer) == self._buffer.maxlen and self._buffer[0][1] == "llm":
            self._llm_count -= 1
        self._buffer.append((monotonic_ns, routing_path))
        if routing_path == "llm":
            self._llm_count += 1

    def qsize(self) -> int:
        """Return the current resident-event count in the buffer."""
        return len(self._buffer)

    def routing_paths(self) -> list[RoutingPath]:
        """Return a snapshot of the routing paths in the buffer.

        Public accessor for callers (tests, ``/readyz``) that need the
        current buffer composition without reaching into ``_buffer``.
        Order matches insertion order (oldest first).
        """
        return [path for _, path in self._buffer]

    def _evict_older_than_window(self, *, now_ns: int) -> None:
        """Pop entries whose age exceeds ``window_sec``.

        Predicate is ``age > window_sec``: events at exactly the
        edge (``age == window_sec``) survive; events older by 1 ns
        are evicted. This pins the off-by-one behaviour the
        window-rollover test asserts.

        ``time.monotonic_ns()`` is non-decreasing per PEP 418, so
        ``now_ns < buffer[0][0]`` cannot happen in production. Synthetic
        test inputs that violate that invariant cause this loop to exit
        immediately (no eviction); that is acceptable because production
        code never hits the path.
        """
        window_ns = self._window_sec * 1_000_000_000
        while self._buffer and (now_ns - self._buffer[0][0]) > window_ns:
            _, evicted_path = self._buffer.popleft()
            if evicted_path == "llm":
                self._llm_count -= 1

    def compute_rate(self, *, now_ns: int) -> float:
        """Compute ``count(routing_path == "llm") / count(*)`` over the rolling window.

        Eviction runs first so a stale buffer doesn't poison the
        denominator. Empty buffer → rate 0.0 (no events to divide by).
        Uses the running ``_llm_count`` so the numerator is O(1).
        """
        self._evict_older_than_window(now_ns=now_ns)
        total = len(self._buffer)
        if total == 0:
            return 0.0
        return self._llm_count / total

    # -----------------------------------------------------------------
    # Webhook firing
    # -----------------------------------------------------------------

    def _build_payload(self, *, rate: float, fired_at_unix: float) -> DriftPayload:
        """Build the four-key JSON body the webhook receives.

        Body shape locked by the plan: ``{window_sec, rate, threshold,
        fired_at_unix}``. No extra keys — drift is a notification, the
        dashboard owns breakdowns.
        """
        return {
            "window_sec": self._window_sec,
            "rate": rate,
            "threshold": self._threshold,
            "fired_at_unix": fired_at_unix,
        }

    async def check_and_fire(self, *, now_ns: int) -> None:
        """Compute the rolling rate; fire the webhook if over threshold.

        Eviction always runs so the buffer stays bounded by time even
        when ``webhook_url`` is None (the disabled-firing path).
        Disabled cleanly when ``webhook_url`` is None (no httpx client
        opens). Any failure (timeout, connection refused, non-2xx,
        TLS) increments ``counters.drift_webhook_failures`` and
        returns — no retry, the next interval will fire again if drift
        persists.

        Failure logging deliberately omits ``exc.__str__`` because
        ``httpx.RequestError.__str__`` includes the full URL, which can
        embed credentials (``https://user:secret@host``). Logging only
        ``type(exc).__name__`` mirrors the redaction discipline in
        ``samantha_server.llm.omlx_client``.
        """
        rate = self.compute_rate(now_ns=now_ns)
        if self._webhook_url is None:
            return
        if rate <= self._threshold:
            return

        payload = self._build_payload(rate=rate, fired_at_unix=time.time())

        # New AsyncClient per fire is correct at one-per-check_interval
        # cadence: cost is dominated by webhook RTT, and the ephemeral
        # client avoids coupling a persistent connection pool to
        # ``aclose()``. Do not "optimise" without re-evaluating shutdown
        # semantics.
        try:
            async with httpx.AsyncClient(timeout=_HTTPX_TIMEOUT_SEC) as client:
                response = await client.post(self._webhook_url, json=payload)
        except (httpx.RequestError, httpx.InvalidURL, OSError) as exc:
            # ``httpx.InvalidURL`` is a sibling of ``httpx.RequestError``,
            # not a subclass — it must be listed explicitly. Without
            # this catch a malformed webhook URL would propagate past
            # the failure counter as a bare exception.
            self._counters.drift_webhook_failures.increment()
            _log.warning(
                "Drift webhook request failed; counter incremented (no retry per G21): %s",
                type(exc).__name__,
            )
            return

        if not response.is_success:
            self._counters.drift_webhook_failures.increment()
            _log.warning(
                "Drift webhook returned non-2xx; counter incremented (no retry per G21): status=%d",
                response.status_code,
            )

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    @property
    def check_interval_sec(self) -> int:
        """Lifespan-task wake interval (read by the loop driver)."""
        return self._check_interval_sec


__all__ = ["DriftMonitor", "DriftPayload", "RoutingPath"]
