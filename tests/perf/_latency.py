"""Latency measurement helper for perf benchmarks.

Uses ``time.perf_counter_ns()`` for nanosecond resolution and disables the
CPython GC during measurement to avoid stop-the-world pauses (which can be
tens of microseconds on a full collection and would contaminate per-call deltas).
Returns the *true median* per-call latency in seconds.

Why median and not mean?
- Median is robust to the occasional OS scheduling preemption that inflates a
  single batch.  A regression must push the median above the ceiling, which
  requires the majority of batches to regress — a false alarm from one noisy
  batch is impossible.

Measurement methodology (batch timing):
- Rather than one ``perf_counter_ns()`` call per iteration (which adds ~50–200 ns
  of clock overhead per sample on darwin-arm64), we time *batches* of calls:
  one clock pair wraps ``batch_size`` inner iterations, and the per-call latency
  for that batch is ``(t1 - t0) / batch_size``.  This amortises clock overhead
  from ~150 ns/call down to ~0.15 ns/call.
- We collect ``iterations // batch_size`` batch samples (default: 10 batches of
  1,000 calls each).  ``statistics.median()`` across those samples is reported.
  This is a "median of means" — each sample is already an average over one
  batch.  The trade-off (less granular tail distribution) is acceptable because
  GC is disabled and batch variance is dominated by CPU scheduling jitter, not
  within-batch variation.
- Warm-up: none.  Cold-start samples land in the lower tail of the distribution
  and are removed by the median; adding warm-up would introduce speculative
  abstraction for a case that does not affect the reported statistic.
"""

import gc
import statistics
import time
from collections.abc import Callable


def measure_latency_distribution(
    callable_: Callable[[], object],
    iterations: int = 10_000,
    batch_size: int = 1000,
) -> dict[str, float]:
    """Return a distribution summary of per-call latency in seconds.

    Disables the CPython GC during the measurement window so that GC pauses do
    not inflate individual samples.  GC is always re-enabled in the finally
    block regardless of exceptions.

    Methodology: times *batches* of ``batch_size`` calls with one clock pair
    per batch.  Each batch sample is ``(t1 - t0) / batch_size``.  Returns a
    dict with ``median``, ``p95``, and ``p99`` keys (all in seconds).

    Args:
        callable_: A zero-argument callable whose per-call latency is measured.
                   Construction / warm-up should be done *before* calling this
                   function; only the raw invocation is timed.
        iterations: Total number of calls to make.  Must be >= 1.
        batch_size: Calls per batch (clock pair).  Must be >= 1.
                    If ``iterations < batch_size``, a single batch of
                    ``iterations`` calls is used.

    Returns:
        Dict with ``median``, ``p95``, and ``p99`` per-call durations in seconds.
        ``median`` is the true median (averages the two middle samples for even
        batch counts via ``statistics.median()``).
    """
    if iterations < 1:
        raise ValueError(f"iterations must be >= 1, got {iterations}")
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    # Round down to the nearest whole batch; fall back to a single batch if
    # iterations < batch_size.
    n_batches = iterations // batch_size
    if n_batches < 1:
        n_batches = 1
        batch_size = iterations

    samples: list[float] = []
    gc.disable()
    try:
        for _ in range(n_batches):
            t0 = time.perf_counter_ns()
            for _ in range(batch_size):
                callable_()
            t1 = time.perf_counter_ns()
            samples.append((t1 - t0) / batch_size)
    finally:
        gc.enable()

    assert len(samples) == n_batches
    samples_sorted = sorted(samples)
    n = len(samples_sorted)
    return {
        "median": statistics.median(samples_sorted) / 1e9,
        "p95": samples_sorted[max(0, int(n * 0.95) - 1)] / 1e9,
        "p99": samples_sorted[max(0, int(n * 0.99) - 1)] / 1e9,
    }


def measure_median_latency_seconds(
    callable_: Callable[[], object],
    iterations: int = 10_000,
    batch_size: int = 1000,
) -> float:
    """Return median per-call latency in seconds using batch timing.

    Delegates to ``measure_latency_distribution`` and returns the ``median``
    value.  See that function for full methodology documentation.

    Args:
        callable_: A zero-argument callable whose per-call latency is measured.
                   Construction / warm-up should be done *before* calling this
                   function; only the raw invocation is timed.
        iterations: Total number of calls to make.  Must be >= 1.
        batch_size: Calls per batch (clock pair).  Must be >= 1.
                    If ``iterations < batch_size``, a single batch of
                    ``iterations`` calls is used.

    Returns:
        Median per-call duration in seconds (true median — averages the two
        middle samples for even batch counts via ``statistics.median()``).
    """
    return measure_latency_distribution(callable_, iterations, batch_size)["median"]
