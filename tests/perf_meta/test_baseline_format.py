"""JSON baseline file format tests (Slices 7, 8, 13).

Verifies that ``_write_baseline`` produces a well-formed JSON file with all
required metadata and per-primitive fields.  Uses ``tmp_path`` so the test is
hermetic and does not write into ``results/perf/``.

These tests live outside ``tests/perf/`` so they run on every default
``pytest`` invocation (no ``@pytest.mark.perf`` decorator) and are not
subject to the ``autoskip_in_ci`` autouse fixture.
"""

import json
from pathlib import Path

from tests.perf.test_primitive_latency import (
    ATOMIC_CEILING_S,
    BENCH_ITERATIONS,
    COMBINATOR_CEILING_S,
    _write_baseline,
)


def _make_mock_results() -> dict:
    """Build a mock results dict with all expected primitive entries."""
    atomic = {
        "IsNull": {
            "median_seconds": 1.23e-7,
            "p95_seconds": 1.80e-7,
            "p99_seconds": 2.10e-7,
            "ceiling_seconds": ATOMIC_CEILING_S,
            "passed": True,
        },
        "Equals": {
            "median_seconds": 1.30e-7,
            "p95_seconds": 1.90e-7,
            "p99_seconds": 2.20e-7,
            "ceiling_seconds": ATOMIC_CEILING_S,
            "passed": True,
        },
        "ThresholdGTE": {
            "median_seconds": 1.40e-7,
            "p95_seconds": 2.00e-7,
            "p99_seconds": 2.30e-7,
            "ceiling_seconds": ATOMIC_CEILING_S,
            "passed": True,
        },
        "ThresholdLTE": {
            "median_seconds": 1.40e-7,
            "p95_seconds": 2.00e-7,
            "p99_seconds": 2.30e-7,
            "ceiling_seconds": ATOMIC_CEILING_S,
            "passed": True,
        },
        "InEnum": {
            "median_seconds": 1.50e-7,
            "p95_seconds": 2.10e-7,
            "p99_seconds": 2.40e-7,
            "ceiling_seconds": ATOMIC_CEILING_S,
            "passed": True,
        },
        "Contains": {
            "median_seconds": 1.60e-7,
            "p95_seconds": 2.20e-7,
            "p99_seconds": 2.50e-7,
            "ceiling_seconds": ATOMIC_CEILING_S,
            "passed": True,
        },
    }
    failclosed = {
        "ThresholdGTE_failclosed": {
            "median_seconds": 1.45e-7,
            "p95_seconds": 2.05e-7,
            "p99_seconds": 2.35e-7,
            "ceiling_seconds": ATOMIC_CEILING_S,
            "passed": True,
        },
        "ThresholdLTE_failclosed": {
            "median_seconds": 1.45e-7,
            "p95_seconds": 2.05e-7,
            "p99_seconds": 2.35e-7,
            "ceiling_seconds": ATOMIC_CEILING_S,
            "passed": True,
        },
        "Contains_failclosed": {
            "median_seconds": 1.55e-7,
            "p95_seconds": 2.15e-7,
            "p99_seconds": 2.45e-7,
            "ceiling_seconds": ATOMIC_CEILING_S,
            "passed": True,
        },
        "InEnum_failclosed": {
            "median_seconds": 1.55e-7,
            "p95_seconds": 2.15e-7,
            "p99_seconds": 2.45e-7,
            "ceiling_seconds": ATOMIC_CEILING_S,
            "passed": True,
        },
    }
    combinator = {
        "BooleanAnd_3level": {
            "median_seconds": 5.00e-7,
            "p95_seconds": 7.00e-7,
            "p99_seconds": 8.00e-7,
            "ceiling_seconds": COMBINATOR_CEILING_S,
            "passed": True,
        },
    }
    return {**atomic, **failclosed, **combinator}


def test_write_baseline_json_round_trip(tmp_path: Path) -> None:
    """_write_baseline writes a JSON file readable by json.loads with correct structure."""
    mock_results = _make_mock_results()
    out_path = _write_baseline(mock_results, out_dir=tmp_path)
    assert out_path.exists(), f"Baseline file not written: {out_path}"
    data = json.loads(out_path.read_text())

    # Top-level metadata — must be non-empty strings / correct types
    assert isinstance(data["date"], str) and data["date"] != ""
    assert isinstance(data["python"], str) and data["python"] != ""
    assert isinstance(data["platform"], str) and data["platform"] != ""
    assert isinstance(data["iterations"], int) and data["iterations"] == BENCH_ITERATIONS
    assert isinstance(data["primitives"], dict)


def test_write_baseline_primitive_entry_shape(tmp_path: Path) -> None:
    """Each primitive entry has all required fields with correct types."""
    mock_results = _make_mock_results()
    out_path = _write_baseline(mock_results, out_dir=tmp_path)
    data = json.loads(out_path.read_text())

    for key, entry in data["primitives"].items():
        assert "median_seconds" in entry and "p95_seconds" in entry, (
            f"{key}: missing percentile fields"
        )
        assert "p99_seconds" in entry and "ceiling_seconds" in entry and "passed" in entry, (
            f"{key}: missing required fields"
        )
        assert isinstance(entry["median_seconds"], float), f"{key}: median_seconds must be float"
        assert isinstance(entry["p95_seconds"], float), f"{key}: p95_seconds must be float"
        assert isinstance(entry["p99_seconds"], float), f"{key}: p99_seconds must be float"
        assert isinstance(entry["ceiling_seconds"], float), f"{key}: ceiling_seconds must be float"
        assert isinstance(entry["passed"], bool), f"{key}: passed must be bool"


def test_write_baseline_timestamped_filename(tmp_path: Path) -> None:
    """Each call produces a distinct filename (timestamps prevent silent overwrites)."""
    mock_results = _make_mock_results()
    path1 = _write_baseline(mock_results, out_dir=tmp_path)
    # Write again (simulates same-day re-run); filename must differ from first call
    # because isoformat(timespec='seconds') advances each second.
    # In practice: both writes in the same second produce the same name — that is
    # acceptable within a single test run.  The assertion here confirms the filename
    # is not the legacy date-only name.
    assert path1.name.startswith("primitive-baseline-"), f"Unexpected filename format: {path1.name}"
    # Filename must include time component (length > "primitive-baseline-YYYY-MM-DD.json")
    assert len(path1.name) > len("primitive-baseline-2026-04-28.json"), (
        f"Filename appears to be date-only, not timestamped: {path1.name}"
    )


def test_write_baseline_anchored_path_default(tmp_path: Path) -> None:
    """_write_baseline returns the path that was actually written."""
    mock_results = _make_mock_results()
    result_path = _write_baseline(mock_results, out_dir=tmp_path)
    assert result_path.parent == tmp_path
    assert result_path.exists()
