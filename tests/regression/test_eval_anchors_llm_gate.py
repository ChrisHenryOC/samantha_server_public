"""Behavioral tests for the LLM-path regression gate (GH-90).

Drives `_evaluate_anchors` directly with synthetic `AccuracyReport` instances
so every branch (deferred soft-fail, floor-met enforcement, breach, sample/p99
mismatch) has explicit coverage. The CI test in `test_eval_anchors.py` runs
`replay()` against the real corpus; this file isolates the gate logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from samantha_server.scenarios.replay import AccuracyReport, replay
from tests.regression.test_eval_anchors import (
    _P99_LLM_SAMPLE_FLOOR,
    _REQUIRED_TREND_KEYS,
    P99_LATENCY_US_LLM_THRESHOLD,
    P99_LATENCY_US_THRESHOLD,
    TrendSummary,
    _evaluate_anchors,
    _make_dispatch_via_evaluate_patch,
    _make_mock_llm_client,
)

# ---------------------------------------------------------------------------
# Fixture builder — synthetic AccuracyReport with everything dialled in.
# ---------------------------------------------------------------------------


def _report(
    *,
    included_accuracy: float = 1.0,
    p99_latency_us: int | None = 92,
    p99_latency_us_llm: int | None = 83,
    deterministic_count: int = 1000,
    llm_count: int = 0,
) -> AccuracyReport:
    """Build an AccuracyReport with only the fields _evaluate_anchors reads.

    Defaults form a passing-on-deterministic-only report (LLM gate deferred).
    """
    return AccuracyReport(
        included_accuracy=included_accuracy,
        overall_accuracy=included_accuracy,
        included_total=100,
        overall_total=100,
        included_pass=int(round(included_accuracy * 100)),
        overall_pass=int(round(included_accuracy * 100)),
        scenario_verdicts=(),
        p99_latency_us=p99_latency_us,
        p99_latency_us_llm=p99_latency_us_llm,
        deterministic_latency_step_count=deterministic_count,
        llm_latency_step_count=llm_count,
    )


# ---------------------------------------------------------------------------
# Module-constant smoke tests (kept from the prior file)
# ---------------------------------------------------------------------------


def test_trend_summary_declares_p99_latency_us_llm() -> None:
    assert "p99_latency_us_llm" in TrendSummary.__annotations__


def test_required_trend_keys_includes_p99_latency_us_llm() -> None:
    assert "p99_latency_us_llm" in _REQUIRED_TREND_KEYS


def test_p99_llm_sample_floor_matches_cli_anchor() -> None:
    """The CI regression floor must match the CLI's runtime floor
    (``samantha_server.scenarios.replay._LLM_ANCHOR_MIN_STEP_COUNT``) so
    the gate-deferred-vs-enforced cutover is the same on both surfaces."""
    from samantha_server.scenarios.replay import _LLM_ANCHOR_MIN_STEP_COUNT

    assert _P99_LLM_SAMPLE_FLOOR == _LLM_ANCHOR_MIN_STEP_COUNT


def test_p99_latency_us_llm_threshold_matches_production_anchor() -> None:
    """The CI regression threshold must match the CLI's runtime anchor
    (``samantha_server.scenarios.replay._LLM_LATENCY_ANCHOR_US``) so a
    partial bump (one literal updated, the other forgotten) cannot ship
    green.

    Mirrors the floor-parity test (`test_p99_llm_sample_floor_matches_cli_anchor`)
    one finding above. PR #276 review #1 — three reviewers independently
    flagged the absence of this parity test as a silent-failure risk.
    """
    from samantha_server.scenarios.replay import _LLM_LATENCY_ANCHOR_US

    assert P99_LATENCY_US_LLM_THRESHOLD == _LLM_LATENCY_ANCHOR_US


# ---------------------------------------------------------------------------
# H-03: Deterministic gate paths
# ---------------------------------------------------------------------------


def test_evaluate_anchors_passes_when_everything_clean() -> None:
    failures, info = _evaluate_anchors(_report())
    assert failures == []
    # n=0 < floor → deferral notice expected
    assert info is not None and "deferred" in info


def test_evaluate_anchors_flags_low_accuracy() -> None:
    failures, _ = _evaluate_anchors(_report(included_accuracy=0.99))
    assert any("included_accuracy" in f for f in failures)


def test_evaluate_anchors_flags_deterministic_p99_breach() -> None:
    failures, _ = _evaluate_anchors(_report(p99_latency_us=P99_LATENCY_US_THRESHOLD))
    assert any("p99_latency_us" in f and "p99_latency_us_llm" not in f for f in failures)


def test_evaluate_anchors_flags_none_deterministic_p99() -> None:
    failures, _ = _evaluate_anchors(_report(p99_latency_us=None))
    assert any("p99_latency_us is None" in f for f in failures)


# ---------------------------------------------------------------------------
# H-03: LLM gate — deferred / enforced boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1, 29])
def test_evaluate_anchors_defers_llm_below_floor(n: int) -> None:
    failures, info = _evaluate_anchors(_report(llm_count=n))
    assert info is not None
    assert f"n={n}" in info
    assert f"floor={_P99_LLM_SAMPLE_FLOOR}" in info
    # No LLM-related failure when below the floor.
    assert not any("p99_latency_us_llm" in f for f in failures)


def test_evaluate_anchors_enforces_at_floor() -> None:
    failures, info = _evaluate_anchors(
        _report(llm_count=_P99_LLM_SAMPLE_FLOOR, p99_latency_us_llm=83)
    )
    # At the floor exactly: no deferral, no failure (passing run).
    assert info is None
    assert failures == []


def test_evaluate_anchors_flags_llm_breach_at_floor() -> None:
    failures, info = _evaluate_anchors(
        _report(
            llm_count=_P99_LLM_SAMPLE_FLOOR,
            p99_latency_us_llm=P99_LATENCY_US_LLM_THRESHOLD,
        )
    )
    assert info is None
    assert any(
        "p99_latency_us_llm" in f and str(P99_LATENCY_US_LLM_THRESHOLD) in f for f in failures
    )


def test_evaluate_anchors_flags_llm_breach_above_floor() -> None:
    failures, _ = _evaluate_anchors(
        _report(
            llm_count=200,
            p99_latency_us_llm=P99_LATENCY_US_LLM_THRESHOLD + 1_000_000,
        )
    )
    assert any("p99_latency_us_llm" in f for f in failures)


def test_evaluate_anchors_surfaces_count_population_mismatch() -> None:
    """Floor met but p99_latency_us_llm is None — a future filter drift bug.
    The gate must surface this loudly (failure), not silently bypass."""
    failures, info = _evaluate_anchors(
        _report(llm_count=_P99_LLM_SAMPLE_FLOOR, p99_latency_us_llm=None)
    )
    assert info is None
    assert any("sample-count / p99-population mismatch" in f for f in failures)


# ---------------------------------------------------------------------------
# H-01: gate_passed reflects ALL three anchors (not just the deterministic two)
# ---------------------------------------------------------------------------


def test_evaluate_anchors_only_llm_breaks() -> None:
    """When only the LLM anchor fails, the failures list still has one entry —
    so the test's `assert not failures` will fire and `gate_passed = not failures`
    will end up False (the H-01 fix)."""
    failures, _ = _evaluate_anchors(
        _report(
            included_accuracy=1.0,
            p99_latency_us=92,
            llm_count=_P99_LLM_SAMPLE_FLOOR,
            p99_latency_us_llm=P99_LATENCY_US_LLM_THRESHOLD + 1,
        )
    )
    assert len(failures) == 1
    assert "p99_latency_us_llm" in failures[0]
    # gate_passed is computed in the test as `not failures`; here it would be False.


def test_evaluate_anchors_collects_all_three_failures_in_one_run() -> None:
    failures, _ = _evaluate_anchors(
        _report(
            included_accuracy=0.5,
            p99_latency_us=P99_LATENCY_US_THRESHOLD + 1,
            llm_count=_P99_LLM_SAMPLE_FLOOR,
            p99_latency_us_llm=P99_LATENCY_US_LLM_THRESHOLD + 1,
        )
    )
    assert len(failures) == 3


# ---------------------------------------------------------------------------
# Trend-file plumbing
# ---------------------------------------------------------------------------


def test_required_trend_keys_present_after_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-09: synthesize a trend file via replay() against a minimal corpus
    in tmp_path so the test does not depend on a stateful results directory.

    Uses the vendored corpus path because replay() requires loadable scenarios;
    only the trend file write is redirected to tmp_path.
    """
    from tests.regression import test_eval_anchors as anchors_mod

    monkeypatch.setattr(anchors_mod, "_P99_SAMPLE_FLOOR", 1)  # let any corpus pass the floor

    vendored = Path(__file__).parent.parent / "fixtures" / "scenarios"
    if not vendored.exists():
        pytest.skip("No vendored corpus to drive replay()")

    from samantha_server.rules.loader import RuleIndex, load_rule_specs

    specs_dir = Path(__file__).parents[2] / "samantha_server" / "rules" / "specs"
    rule_index = RuleIndex(load_rule_specs(specs_dir))
    mock_llm = _make_mock_llm_client()

    with patch(
        "samantha_server.api.routing.dispatch_event",
        side_effect=_make_dispatch_via_evaluate_patch(rule_index),
    ):
        report = replay(vendored, rule_index=rule_index, _llm_client_override=mock_llm)
    failures, info = _evaluate_anchors(report)
    assert info is not None or not failures, "Test corpus unexpectedly broke a gate"

    # Build the same trend dict that test_eval_regression_anchors writes.
    summary: TrendSummary = {
        "timestamp": "test",
        "included_accuracy": report.included_accuracy,
        "p99_latency_us": report.p99_latency_us if report.p99_latency_us is not None else 0,
        "p99_latency_us_llm": report.p99_latency_us_llm,
        "step_count": sum(len(v.step_verdicts) for v in report.scenario_verdicts),
        "included_pass": report.included_pass,
        "included_total": report.included_total,
        "gate_passed": not failures,
    }
    target = tmp_path / "trend.json"
    target.write_text(json.dumps(summary))
    parsed = json.loads(target.read_text())
    assert parsed.keys() >= _REQUIRED_TREND_KEYS


def test_p99_latency_us_llm_serializes_as_null_when_none(tmp_path: Path) -> None:
    """M-08: when p99_latency_us_llm is None, the trend file's JSON value must be `null`."""
    summary = {
        "timestamp": "test",
        "included_accuracy": 1.0,
        "p99_latency_us": 92,
        "p99_latency_us_llm": None,
        "step_count": 100,
        "included_pass": 100,
        "included_total": 100,
        "gate_passed": True,
    }
    path = tmp_path / "trend.json"
    path.write_text(json.dumps(summary))
    raw = path.read_text()
    assert '"p99_latency_us_llm": null' in raw
    parsed = json.loads(raw)
    assert parsed["p99_latency_us_llm"] is None
