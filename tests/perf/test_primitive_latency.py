"""Primitive latency micro-benchmarks (Step 4).

Structural notes
----------------
- Each perf test is marked ``@pytest.mark.perf`` and is excluded from the
  default ``pytest`` run via ``addopts = "-m 'not perf'"`` in pyproject.toml.
  Run explicitly with: ``pytest -m perf``
- When the ``CI`` env var is set (GitHub Actions and most hosted CI systems),
  the ``autoskip_in_ci`` autouse fixture in ``tests/perf/conftest.py`` skips
  every test in this directory.
- Each run writes a timestamped JSON baseline to ``results/perf/`` via the
  ``baseline_recorder`` session fixture.

Latency ceilings (from docs/plans/phase-1-implementation.md § Step 4)
----------------------------------------------------------------------
- Atomic primitives:          < 100 µs  median per evaluation
- 3-level-nested combinator:  < 500 µs  median per evaluation

Spec alignment: 9 primitives total (IsNull, Equals, ThresholdGTE,
ThresholdLTE, InEnum, Contains, BooleanAnd, BooleanOr, Not).  The atomic
bench covers 6 leaf primitives; the combinator bench exercises BooleanAnd,
BooleanOr, and Not together in a single realistic IHC-001 shape — one
representative shape provides stronger canary signal than three synthetic
single-combinator shapes.
"""

import datetime
import functools
import json
import platform
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from samantha_server.models import Event, Order, SpecimenContext
from samantha_server.primitives import (
    BooleanAnd,
    BooleanOr,
    Contains,
    Equals,
    InEnum,
    IsNull,
    Not,
    ThresholdGTE,
    ThresholdLTE,
)
from tests.perf._latency import measure_latency_distribution

# ---------------------------------------------------------------------------
# Latency ceilings
# ---------------------------------------------------------------------------

ATOMIC_CEILING_S = 100e-6  # 100 µs
COMBINATOR_CEILING_S = 500e-6  # 500 µs
BENCH_ITERATIONS = 10_000
BENCH_BATCH_SIZE = 1000

# ---------------------------------------------------------------------------
# Fixed SpecimenContext for all bench measurements
# (built once; frozen Pydantic is safe to share across tests)
# ---------------------------------------------------------------------------


def _make_bench_context() -> SpecimenContext:
    """Build a realistic SpecimenContext matching the HER2/fixation rule corpus."""
    order = Order(
        order_id="BENCH-001",
        patient_name="Jane Doe",
        patient_sex="F",
        age=45,
        specimen_type="core_needle_biopsy",
        anatomic_site="breast",
        fixative="formalin",
        fixation_time_hours=24.0,
        ordered_tests=("ER", "PR", "HER2"),
        priority="routine",
        billing_info_present=True,
    )
    event = Event(
        event_type="order_received",
        event_data={"added_markers": ["HER2"]},
        step_index=0,
    )
    return SpecimenContext(
        order=order,
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=event,
    )


_BENCH_CTX = _make_bench_context()

# ---------------------------------------------------------------------------
# Fixed SpecimenContext with deliberately mis-typed fields for fail-closed bench
# ---------------------------------------------------------------------------


def _make_failclosed_context() -> SpecimenContext:
    """Build a context that exercises the TypeError → False fail-closed paths.

    Uses event_data fields with unexpected types to trigger the except branches
    in ThresholdGTE, ThresholdLTE, Contains, and InEnum.
    """
    order = Order(
        order_id="BENCH-FC-001",
        patient_name="Jane Doe",
        patient_sex="F",
        age=45,
        specimen_type="core_needle_biopsy",
        anatomic_site="breast",
        fixative="formalin",
        fixation_time_hours=24.0,
        ordered_tests=("ER", "PR", "HER2"),
        priority="routine",
        billing_info_present=True,
    )
    event = Event(
        event_type="order_received",
        # scoreless: string where numeric expected → ThresholdGTE/LTE TypeError
        # count: int where iterable expected → Contains TypeError
        # items: list where hashable expected → InEnum TypeError (unhashable list)
        event_data={"scoreless": "N/A", "count": 5, "items": [1, 2, 3]},
        step_index=0,
    )
    return SpecimenContext(
        order=order,
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=event,
    )


_FAILCLOSED_CTX = _make_failclosed_context()

# ---------------------------------------------------------------------------
# Baseline JSON writer
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = PROJECT_ROOT / "results" / "perf"


def _write_baseline(results: dict[str, Any], out_dir: Path | None = None) -> Path:
    """Write a timestamped JSON baseline file.

    Args:
        results: The per-primitive measurement dict to record.
        out_dir: Directory to write into.  Defaults to
                 ``<repo-root>/results/perf/``, anchored to this file's
                 location — immune to CWD changes.

    Returns:
        The ``Path`` of the written file.
    """
    target = out_dir if out_dir is not None else DEFAULT_OUT_DIR
    target.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().isoformat(timespec="seconds").replace(":", "-")
    out_path = target / f"primitive-baseline-{ts}.json"
    payload = {
        "date": datetime.date.today().isoformat(),
        "python": sys.version.split()[0],
        "platform": f"{sys.platform}-{platform.machine()}",
        "iterations": BENCH_ITERATIONS,
        "primitives": results,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path


# ---------------------------------------------------------------------------
# Ceiling assertion helper (also exercised in tests/perf_meta/)
# ---------------------------------------------------------------------------


def _assert_under_ceiling(name: str, median_s: float, ceiling_s: float) -> None:
    """Assert median latency is below the ceiling; surface name and µs on failure."""
    assert median_s < ceiling_s, (
        f"{name}.evaluate() median={median_s * 1e6:.1f} µs exceeds ceiling {ceiling_s * 1e6:.0f} µs"
    )


# ---------------------------------------------------------------------------
# Baseline JSON recorder (session-scoped)
# ---------------------------------------------------------------------------

# Expected number of primitive entries populated by the bench tests.
_EXPECTED_KEY_COUNT = 11  # 6 atomic + 4 fail-closed + 1 combinator


@pytest.fixture(scope="session")
def baseline_recorder() -> Generator[dict[str, Any], None, None]:
    """Session fixture: collect per-primitive results; write JSON on teardown.

    Teardown skips the file write when the recorder is empty (bench tests were
    deselected or the session had no bench runs).
    """
    results: dict[str, Any] = {}
    yield results
    if results:
        _write_baseline(results)
    # else: bench tests were deselected; do not write an empty baseline file.


# ---------------------------------------------------------------------------
# Atomic primitive benchmarks
# ---------------------------------------------------------------------------

# Each tuple: (name, primitive_instance)
_ATOMIC_CASES: list[tuple[str, Any]] = [
    ("IsNull", IsNull(field="patient_name")),
    ("Equals", Equals(field="fixative", value="formalin")),
    ("ThresholdGTE", ThresholdGTE(field="fixation_time_hours", value=6.0)),
    ("ThresholdLTE", ThresholdLTE(field="fixation_time_hours", value=72.0)),
    ("InEnum", InEnum(field="current_state", values=frozenset({"ACCESSIONING", "ACCEPTED"}))),
    ("Contains", Contains(field="ordered_tests", value="HER2")),
]


@pytest.mark.perf
@pytest.mark.parametrize("name,prim", _ATOMIC_CASES, ids=[c[0] for c in _ATOMIC_CASES])
def test_atomic_primitive_latency(
    name: str,
    prim: Any,
    baseline_recorder: dict[str, Any],
) -> None:
    """Each atomic primitive evaluates in < 100 µs (median over 10,000 calls)."""
    dist = measure_latency_distribution(
        functools.partial(prim.evaluate, _BENCH_CTX),
        iterations=BENCH_ITERATIONS,
        batch_size=BENCH_BATCH_SIZE,
    )
    baseline_recorder[name] = {
        "median_seconds": dist["median"],
        "p95_seconds": dist["p95"],
        "p99_seconds": dist["p99"],
        "ceiling_seconds": ATOMIC_CEILING_S,
        "passed": dist["median"] < ATOMIC_CEILING_S,
    }
    _assert_under_ceiling(name, dist["median"], ATOMIC_CEILING_S)


# ---------------------------------------------------------------------------
# Fail-closed benchmarks (TypeError → False paths)
# ---------------------------------------------------------------------------

# Each tuple: (name, primitive_instance).
# These primitives will encounter type mismatches in _FAILCLOSED_CTX and
# exercise the except TypeError → return False branches.
_FAILCLOSED_CASES: list[tuple[str, Any]] = [
    # "N/A" is a string; >= float raises TypeError → False
    ("ThresholdGTE_failclosed", ThresholdGTE(field="event.scoreless", value=6.0)),
    # Same pattern for LTE
    ("ThresholdLTE_failclosed", ThresholdLTE(field="event.scoreless", value=72.0)),
    # 5 (int) is non-iterable; 'HER2' in 5 raises TypeError → False
    ("Contains_failclosed", Contains(field="event.count", value="HER2")),
    # [1,2,3] is a list (unhashable); [1,2,3] in frozenset raises TypeError → False
    ("InEnum_failclosed", InEnum(field="event.items", values=frozenset({1, 2, 3}))),
]


@pytest.mark.perf
@pytest.mark.parametrize("name,prim", _FAILCLOSED_CASES, ids=[c[0] for c in _FAILCLOSED_CASES])
def test_atomic_primitive_fail_closed_latency(
    name: str,
    prim: Any,
    baseline_recorder: dict[str, Any],
) -> None:
    """Fail-closed TypeError paths also clear the 100 µs ceiling."""
    dist = measure_latency_distribution(
        functools.partial(prim.evaluate, _FAILCLOSED_CTX),
        iterations=BENCH_ITERATIONS,
        batch_size=BENCH_BATCH_SIZE,
    )
    baseline_recorder[name] = {
        "median_seconds": dist["median"],
        "p95_seconds": dist["p95"],
        "p99_seconds": dist["p99"],
        "ceiling_seconds": ATOMIC_CEILING_S,
        "passed": dist["median"] < ATOMIC_CEILING_S,
    }
    _assert_under_ceiling(name, dist["median"], ATOMIC_CEILING_S)


# ---------------------------------------------------------------------------
# Combinator benchmarks
# ---------------------------------------------------------------------------

# IHC-001 shape: BooleanAnd (combinator level 1)
#                  └─ Contains (leaf)
#                  └─ BooleanOr (level 2)
#                       └─ Not (level 3) → Equals (leaf)
#                       └─ Not (level 3) → ThresholdGTE (leaf)
#                       └─ Not (level 3) → ThresholdLTE (leaf)
#
# Three combinator levels; root-to-leaf depth is 4 counting the leaf
# primitive itself.  This is the deepest combinator nesting in the 40-rule
# corpus per docs/rule-breakdown/decision-gate.md.
#
# Evaluation path under _BENCH_CTX: Contains("event.added_markers", "HER2")
# is True (short-circuits BooleanAnd to evaluate the second child), then
# BooleanOr evaluates all three Not(…) children because each Not wraps a
# primitive that is True under the bench context — so Not returns False —
# and BooleanOr has no short-circuit True to fire.  This is the worst-case
# (maximum node visits) path and the correct shape for a ceiling test.
_IHC_001_SHAPE = BooleanAnd(
    children=(
        Contains(field="event.added_markers", value="HER2"),
        BooleanOr(
            children=(
                Not(child=Equals(field="fixative", value="formalin")),
                Not(child=ThresholdGTE(field="fixation_time_hours", value=6.0)),
                Not(child=ThresholdLTE(field="fixation_time_hours", value=72.0)),
            )
        ),
    )
)


@pytest.mark.perf
def test_combinator_3level_latency(baseline_recorder: dict[str, Any]) -> None:
    """3-level-nested combinator (IHC-001 shape) evaluates in < 500 µs (median)."""
    dist = measure_latency_distribution(
        functools.partial(_IHC_001_SHAPE.evaluate, _BENCH_CTX),
        iterations=BENCH_ITERATIONS,
        batch_size=BENCH_BATCH_SIZE,
    )
    baseline_recorder["BooleanAnd_3level"] = {
        "median_seconds": dist["median"],
        "p95_seconds": dist["p95"],
        "p99_seconds": dist["p99"],
        "ceiling_seconds": COMBINATOR_CEILING_S,
        "passed": dist["median"] < COMBINATOR_CEILING_S,
    }
    assert dist["median"] < COMBINATOR_CEILING_S, (
        f"IHC-001 3-level nested median={dist['median'] * 1e6:.1f} µs exceeds ceiling "
        f"{COMBINATOR_CEILING_S * 1e6:.0f} µs"
    )


# ---------------------------------------------------------------------------
# Baseline JSON structural verification
#
# This test verifies the recorder dict has the expected primitive keys and
# entry shape.  File I/O is owned exclusively by the session fixture teardown.
# The JSON round-trip and shape assertions live in tests/perf_meta/test_baseline_format.py.
# ---------------------------------------------------------------------------

_EXPECTED_PRIMITIVE_KEYS = {
    "IsNull",
    "Equals",
    "ThresholdGTE",
    "ThresholdLTE",
    "InEnum",
    "Contains",
    "ThresholdGTE_failclosed",
    "ThresholdLTE_failclosed",
    "Contains_failclosed",
    "InEnum_failclosed",
    "BooleanAnd_3level",
}


@pytest.mark.perf
def test_baseline_json_written(baseline_recorder: dict[str, Any]) -> None:
    """After the bench session, the recorder contains all expected primitive keys."""
    if len(baseline_recorder) < _EXPECTED_KEY_COUNT:
        pytest.skip(
            f"Bench tests deselected or not run in this session "
            f"(got {len(baseline_recorder)} entries, expected {_EXPECTED_KEY_COUNT})"
        )

    assert _EXPECTED_PRIMITIVE_KEYS.issubset(baseline_recorder.keys()), (
        f"Missing keys: {_EXPECTED_PRIMITIVE_KEYS - baseline_recorder.keys()}"
    )

    for key, entry in baseline_recorder.items():
        assert isinstance(entry.get("median_seconds"), float), (
            f"{key}: median_seconds must be float"
        )
        assert isinstance(entry.get("p95_seconds"), float), f"{key}: p95_seconds must be float"
        assert isinstance(entry.get("p99_seconds"), float), f"{key}: p99_seconds must be float"
        assert isinstance(entry.get("ceiling_seconds"), float), (
            f"{key}: ceiling_seconds must be float"
        )
        assert isinstance(entry.get("passed"), bool), f"{key}: passed must be bool"
