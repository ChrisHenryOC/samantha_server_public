"""Tests for samantha_server.observability.counters — AtomicCounter + CounterRegistry."""

from __future__ import annotations

import threading
import time


def test_atomic_counter_starts_at_zero() -> None:
    """AtomicCounter initial value is 0."""
    from samantha_server.observability.counters import AtomicCounter

    c = AtomicCounter()
    assert c.value == 0


def test_atomic_counter_increment_increases_by_one() -> None:
    """increment() adds 1 to the current value."""
    from samantha_server.observability.counters import AtomicCounter

    c = AtomicCounter()
    c.increment()
    assert c.value == 1


def test_atomic_counter_increment_multiple_times() -> None:
    """Multiple increment() calls accumulate correctly."""
    from samantha_server.observability.counters import AtomicCounter

    c = AtomicCounter()
    for _ in range(5):
        c.increment()
    assert c.value == 5


def test_atomic_counter_is_monotonic() -> None:
    """Counter never resets — values never decrease."""
    from samantha_server.observability.counters import AtomicCounter

    c = AtomicCounter()
    previous = c.value
    for _ in range(10):
        c.increment()
        assert c.value >= previous
        previous = c.value


def test_atomic_counter_thread_safety() -> None:
    """Concurrent increments from multiple threads produce the correct total."""
    from samantha_server.observability.counters import AtomicCounter

    c = AtomicCounter()
    n_threads = 20
    increments_per_thread = 100

    def worker() -> None:
        for _ in range(increments_per_thread):
            c.increment()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert c.value == n_threads * increments_per_thread


def test_counter_registry_exposes_named_counters() -> None:
    """CounterRegistry exposes the four named counters as attributes."""
    from samantha_server.observability.counters import CounterRegistry

    reg = CounterRegistry()
    assert hasattr(reg, "otel_export_failures")
    assert hasattr(reg, "drift_webhook_failures")
    assert hasattr(reg, "queue_overflow_rejections")
    assert hasattr(reg, "receipt_signing_failures")


def test_counter_registry_counters_start_at_zero() -> None:
    """All named counters start at 0."""
    from samantha_server.observability.counters import CounterRegistry

    reg = CounterRegistry()
    assert reg.otel_export_failures.value == 0
    assert reg.drift_webhook_failures.value == 0
    assert reg.queue_overflow_rejections.value == 0
    assert reg.receipt_signing_failures.value == 0


def test_counter_registry_snapshot_includes_all_counters() -> None:
    """snapshot() returns a dict with all four counter names."""
    from samantha_server.observability.counters import CounterRegistry

    reg = CounterRegistry()
    snap = reg.snapshot()
    assert "otel_export_failures" in snap
    assert "drift_webhook_failures" in snap
    assert "queue_overflow_rejections" in snap
    assert "receipt_signing_failures" in snap


def test_counter_registry_snapshot_includes_timestamps() -> None:
    """snapshot() includes process_start_unix and snapshot_unix."""
    from samantha_server.observability.counters import CounterRegistry

    reg = CounterRegistry()
    snap = reg.snapshot()
    assert "process_start_unix" in snap
    assert "snapshot_unix" in snap


def test_counter_registry_snapshot_unix_is_recent() -> None:
    """snapshot_unix is within 5 seconds of now."""
    from samantha_server.observability.counters import CounterRegistry

    before = time.time()
    reg = CounterRegistry()
    snap = reg.snapshot()
    after = time.time()
    assert before <= snap["snapshot_unix"] <= after + 1


def test_counter_registry_snapshot_values_are_cumulative() -> None:
    """snapshot() reflects the current cumulative counter values."""
    from samantha_server.observability.counters import CounterRegistry

    reg = CounterRegistry()
    reg.otel_export_failures.increment()
    reg.otel_export_failures.increment()
    reg.receipt_signing_failures.increment()

    snap = reg.snapshot()
    assert snap["otel_export_failures"] == 2
    assert snap["receipt_signing_failures"] == 1
    assert snap["drift_webhook_failures"] == 0


def test_counter_registry_process_start_unix_is_stable() -> None:
    """process_start_unix does not change between snapshots."""
    from samantha_server.observability.counters import CounterRegistry

    reg = CounterRegistry()
    snap1 = reg.snapshot()
    reg.otel_export_failures.increment()
    snap2 = reg.snapshot()
    assert snap1["process_start_unix"] == snap2["process_start_unix"]


def test_counter_registry_has_user_role_coercion_failures() -> None:
    """S2 CounterRegistry exposes user_role_coercion_failures."""
    from samantha_server.observability.counters import CounterRegistry

    reg = CounterRegistry()
    assert hasattr(reg, "user_role_coercion_failures")
    assert reg.user_role_coercion_failures.value == 0


def test_counter_registry_snapshot_includes_user_role_coercion_failures() -> None:
    """S2 snapshot includes user_role_coercion_failures key starting at 0."""
    from samantha_server.observability.counters import CounterRegistry

    reg = CounterRegistry()
    snap = reg.snapshot()
    assert "user_role_coercion_failures" in snap
    assert snap["user_role_coercion_failures"] == 0


def test_counter_registry_has_dispatch_unknown_event_type() -> None:
    """CounterRegistry exposes dispatch_unknown_event_type as an AtomicCounter at 0."""
    from samantha_server.observability.counters import AtomicCounter, CounterRegistry

    reg = CounterRegistry()
    assert hasattr(reg, "dispatch_unknown_event_type")
    assert isinstance(reg.dispatch_unknown_event_type, AtomicCounter)
    assert reg.dispatch_unknown_event_type.value == 0


def test_counter_registry_snapshot_includes_dispatch_unknown_event_type() -> None:
    """snapshot() includes dispatch_unknown_event_type key starting at 0."""
    from samantha_server.observability.counters import CounterRegistry

    reg = CounterRegistry()
    snap = reg.snapshot()
    assert "dispatch_unknown_event_type" in snap
    assert snap["dispatch_unknown_event_type"] == 0
