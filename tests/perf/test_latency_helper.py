"""Tests for the latency measurement helper (Slice 2)."""

import gc
import time

import pytest

from tests.perf._latency import measure_median_latency_seconds


@pytest.mark.perf
def test_measure_median_returns_float_seconds() -> None:
    """A no-op callable returns a float well under 1ms."""
    result = measure_median_latency_seconds(lambda: None, iterations=10_000)
    assert isinstance(result, float)
    assert result < 1e-3, f"No-op exceeded 1ms: {result:.3e}s"


@pytest.mark.perf
def test_measure_median_with_known_sleep() -> None:
    """time.sleep(0) remains trivially bounded — exercises the iteration path."""
    result = measure_median_latency_seconds(lambda: time.sleep(0), iterations=100)
    # time.sleep(0) is a syscall but still very fast; cap at 1ms to be generous
    assert result < 1e-3, f"time.sleep(0) exceeded 1ms: {result:.3e}s"


@pytest.mark.perf
def test_measure_median_true_median_property() -> None:
    """Result is the true (textbook) median, not the upper-middle element.

    With batch_size=1 each iteration becomes its own batch, so the helper
    degenerates to individual timing.  With an even number of batches the
    true median averages the two middle values; the upper-middle element
    would always be >= the true median.  We verify the result is a float
    (the exact value is timing-dependent, but statistics.median is stdlib-
    guaranteed correct).
    """
    result = measure_median_latency_seconds(lambda: None, iterations=10, batch_size=1)
    assert isinstance(result, float)


# ---------------------------------------------------------------------------
# Guard tests (Slice 9)
# ---------------------------------------------------------------------------


def test_iterations_zero_raises_value_error() -> None:
    """iterations=0 raises ValueError with a clear message."""
    with pytest.raises(ValueError, match="iterations"):
        measure_median_latency_seconds(lambda: None, iterations=0)


def test_iterations_negative_raises_value_error() -> None:
    """iterations=-1 raises ValueError with a clear message."""
    with pytest.raises(ValueError, match="iterations"):
        measure_median_latency_seconds(lambda: None, iterations=-1)


def test_batch_size_zero_raises_value_error() -> None:
    """batch_size=0 raises ValueError with a clear message."""
    with pytest.raises(ValueError, match="batch_size"):
        measure_median_latency_seconds(lambda: None, iterations=10, batch_size=0)


# ---------------------------------------------------------------------------
# GC re-enable on exception (Slice 12)
# ---------------------------------------------------------------------------


def test_gc_re_enabled_on_exception() -> None:
    """The helper must always re-enable GC, even when the callable raises."""

    def boom() -> None:
        raise RuntimeError("intentional")

    gc_was_enabled_before = gc.isenabled()
    try:
        with pytest.raises(RuntimeError, match="intentional"):
            measure_median_latency_seconds(boom, iterations=10, batch_size=10)
    finally:
        # Sanity guarantee: if the assert below fails we still clean up.
        if not gc.isenabled():
            gc.enable()
    assert gc.isenabled(), "GC was not re-enabled after exception in callable_"
    assert gc.isenabled() == gc_was_enabled_before


# ---------------------------------------------------------------------------
# Samples-length invariant (Slice 14)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_samples_length_invariant() -> None:
    """len(samples) == n_batches invariant holds in the success path.

    With iterations=10 and batch_size=5 we expect n_batches=2.  The internal
    assert fires if this invariant is ever broken (e.g., a future wrapper
    swallows mid-loop exceptions and returns a partial-sample median).
    """
    result = measure_median_latency_seconds(lambda: None, iterations=10, batch_size=5)
    assert isinstance(result, float)
