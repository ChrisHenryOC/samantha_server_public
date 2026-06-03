"""In-process monotonic counters for silent-loss observability.

AtomicCounter is thread-safe via threading.Lock because some increment
sites originate from inside asyncio.to_thread workers. Bare `int += 1`
is GIL-protected for a single bytecode op, but counter.increment()
decomposes into a method call plus an attribute read-modify-write that
is not atomic across threads. The lock cost is negligible at the POC's
hundreds-of-decisions-per-second ceiling.

CounterRegistry owns a fixed set of named counters; snapshot() returns
a dict with cumulative values + process_start_unix + snapshot_unix.
Counters are monotonic cumulative-since-process-start — never reset,
never windowed.
"""

from __future__ import annotations

import threading
import time


class AtomicCounter:
    """A monotonically-increasing integer counter, safe for concurrent use."""

    def __init__(self) -> None:
        self._value: int = 0
        self._lock: threading.Lock = threading.Lock()

    @property
    def value(self) -> int:
        """Return the current cumulative value."""
        with self._lock:
            return self._value

    def increment(self) -> None:
        """Increment the counter by 1."""
        with self._lock:
            self._value += 1


class CounterRegistry:
    """Registry of named silent-loss counters for the samantha_server orchestrator.

    Named counters:
        otel_export_failures           — OTel trace-export failures (Step 8)
        drift_webhook_failures         — drift-alarm webhook delivery failures (Step 12)
        drift_loop_errors              — uncaught exceptions in the drift-loop iteration (Step 12)
        queue_overflow_rejections      — priority-queue overflow rejections (Step 2)
        receipt_signing_failures       — receipt signing/write failures (Step 1)
        user_role_coercion_failures    — invalid user_role values dropped at extraction (GH-227)
        dispatch_unknown_event_type    — dispatch_empty decisions where event_type is not in the
                                         known event_type set (GH-326)

    snapshot() returns all counter values plus timing metadata for external
    rate computation. Exposed via /readyz when any counter is non-zero.
    """

    def __init__(self) -> None:
        self.otel_export_failures = AtomicCounter()
        self.drift_webhook_failures = AtomicCounter()
        self.drift_loop_errors = AtomicCounter()
        self.queue_overflow_rejections = AtomicCounter()
        self.receipt_signing_failures = AtomicCounter()
        self.user_role_coercion_failures = AtomicCounter()
        self.dispatch_unknown_event_type = AtomicCounter()
        self._process_start_unix: float = time.time()

    def snapshot(self) -> dict[str, float | int]:
        """Return a point-in-time snapshot of all counters plus timing metadata."""
        return {
            "process_start_unix": self._process_start_unix,
            "snapshot_unix": time.time(),
            "otel_export_failures": self.otel_export_failures.value,
            "drift_webhook_failures": self.drift_webhook_failures.value,
            "drift_loop_errors": self.drift_loop_errors.value,
            "queue_overflow_rejections": self.queue_overflow_rejections.value,
            "receipt_signing_failures": self.receipt_signing_failures.value,
            "user_role_coercion_failures": self.user_role_coercion_failures.value,
            "dispatch_unknown_event_type": self.dispatch_unknown_event_type.value,
        }


__all__ = ["AtomicCounter", "CounterRegistry"]
