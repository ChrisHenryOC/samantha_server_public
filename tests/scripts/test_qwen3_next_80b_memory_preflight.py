"""Unit tests for the memory-preflight script's pure helpers.

The script itself requires a live oMLX server and is not reproducible in
CI; the pure classification function IS testable without external state
and is the load-bearing piece (its threshold buckets gate whether a
candidate corpus sweep is safe to queue).
"""

from __future__ import annotations

import pytest

# Threshold contract body:
# - Pass: available > 12 GB (safe — clean headroom for KV cache + OS)
# - Warn: 8 <= available <= 12 GB (tight — proceed but mark candidate `tight`)
# - Fail: available < 8 GB (sweep likely to OOM under sustained load)


def test_classify_pressure_pass_when_well_above_threshold() -> None:
    """Available > 12 GB returns 'pass' (safe to queue sweep)."""
    from scripts.qwen3_next_80b_memory_preflight import _classify_memory_pressure

    assert _classify_memory_pressure(available_gb=20.0) == "pass"
    assert _classify_memory_pressure(available_gb=15.5) == "pass"
    assert _classify_memory_pressure(available_gb=12.01) == "pass"


def test_classify_pressure_warn_when_in_tight_band() -> None:
    """8 <= available <= 12 GB returns 'warn' (tight, proceed with caution)."""
    from scripts.qwen3_next_80b_memory_preflight import _classify_memory_pressure

    assert _classify_memory_pressure(available_gb=12.0) == "warn"
    assert _classify_memory_pressure(available_gb=10.0) == "warn"
    assert _classify_memory_pressure(available_gb=8.0) == "warn"


def test_classify_pressure_fail_when_below_threshold() -> None:
    """Available < 8 GB returns 'fail' (don't queue sweep — OOM risk)."""
    from scripts.qwen3_next_80b_memory_preflight import _classify_memory_pressure

    assert _classify_memory_pressure(available_gb=7.99) == "fail"
    assert _classify_memory_pressure(available_gb=4.0) == "fail"
    assert _classify_memory_pressure(available_gb=0.5) == "fail"


def test_classify_pressure_boundary_8_gb_is_warn_not_fail() -> None:
    """Exact 8.0 GB is the inclusive boundary of warn (not fail).

    Pinning the boundary explicitly so a future maintainer who reads the
    inequality comparisons can match them against the test rather than
    re-deriving from the issue body.
    """
    from scripts.qwen3_next_80b_memory_preflight import _classify_memory_pressure

    assert _classify_memory_pressure(available_gb=8.0) == "warn"
    assert _classify_memory_pressure(available_gb=7.999) == "fail"


def test_classify_pressure_boundary_12_gb_is_warn_not_pass() -> None:
    """Exact 12.0 GB is the inclusive boundary of warn (not pass).

    Mirror of the 8 GB boundary test. The warn band is [8, 12] inclusive
    on both sides; pass starts strictly above 12.
    """
    from scripts.qwen3_next_80b_memory_preflight import _classify_memory_pressure

    assert _classify_memory_pressure(available_gb=12.0) == "warn"
    assert _classify_memory_pressure(available_gb=12.001) == "pass"


@pytest.mark.parametrize("verdict", ["pass", "warn", "fail"])
def test_classify_pressure_returns_only_documented_verdicts(verdict: str) -> None:
    """Sanity check: the documented verdict strings are exactly the three returned."""
    from scripts.qwen3_next_80b_memory_preflight import _classify_memory_pressure

    # Construct a value that should produce each verdict in turn.
    value = {"pass": 20.0, "warn": 10.0, "fail": 4.0}[verdict]
    assert _classify_memory_pressure(available_gb=value) == verdict


def test_classify_pressure_negative_input_returns_fail() -> None:
    """Negative `available_gb` falls into the fail band.

    Defensive: a hypothetical psutil-measurement glitch returning a
    negative value would correctly trigger the strictest verdict. Pin
    so any future inequality flip (e.g., from `<` to `>`) reds.
    """
    from scripts.qwen3_next_80b_memory_preflight import _classify_memory_pressure

    assert _classify_memory_pressure(available_gb=-1.0) == "fail"
    assert _classify_memory_pressure(available_gb=-100.0) == "fail"


def test_system_memory_available_gb_converts_bytes_to_gb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the bytes→GB conversion in `_system_memory_available_gb`.

     was filed because the prior script confused units (ru_maxrss
    on Darwin is bytes, on Linux is kilobytes). The replacement now
    uses psutil which is unambiguous — but explicitly pin the unit
    conversion so a future maintainer rewriting the helper can't
    silently re-introduce the same shape of bug. Monkeypatch psutil
    with a known byte value and assert the GB result.

    Use a value that exposes both the divisor (1024**3) and a
    fractional result so a flipped operator (* vs /) or wrong power
    (1024**2 vs 1024**3) produces a visibly wrong number.
    """
    from types import SimpleNamespace

    import scripts.qwen3_next_80b_memory_preflight as preflight

    # 8 GB exactly = 8 * 1024^3 bytes. If the helper uses 1024^2 it
    # would report 8192.0 (way off); if it uses 1024^4 it would report
    # 0.0078 (also way off); the correct conversion gives 8.0.
    fake_vm = SimpleNamespace(available=8 * 1024**3)
    monkeypatch.setattr(preflight.psutil, "virtual_memory", lambda: fake_vm)

    assert preflight._system_memory_available_gb() == 8.0
