"""Tests for _aggregate_n_sweep_reports (GH-262 Slice 2).

Verifies per-fixture pass/total aggregation across multiple AccuracyReport
instances. Tests corpus-stability assertion (scenario set must be identical
across reports).
"""

from __future__ import annotations

import pytest

from samantha_server.scenarios.replay import (
    AccuracyReport,
    ExpectedOutput,
    PredictedOutput,
    ScenarioVerdict,
    StepVerdict,
    _aggregate_n_sweep_reports,
)


def _make_step(scenario_id: str, *, passed: bool) -> StepVerdict:
    state = "ACCEPTED"
    expected = ExpectedOutput(next_state=state, applied_rules=(), flags=())
    predicted = PredictedOutput(
        next_state=state if passed else "DO_NOT_PROCESS",
        applied_rules=(),
        flags=(),
    )
    return StepVerdict(
        scenario_id=scenario_id,
        step_index=0,
        status="pass" if passed else "mismatch_state",
        expected=expected,
        predicted=predicted,
    )


def _make_verdict(scenario_id: str, *, passed: bool) -> ScenarioVerdict:
    status = "pass" if passed else "fail"
    return ScenarioVerdict(
        scenario_id=scenario_id,
        category="rule_coverage",
        status=status,
        step_verdicts=(_make_step(scenario_id, passed=passed),),
    )


def _make_report(verdicts: list[ScenarioVerdict]) -> AccuracyReport:
    passed = sum(1 for v in verdicts if v.status == "pass")
    return AccuracyReport(
        included_accuracy=passed / len(verdicts) if verdicts else 0.0,
        overall_accuracy=passed / len(verdicts) if verdicts else 0.0,
        included_total=len(verdicts),
        overall_total=len(verdicts),
        included_pass=passed,
        overall_pass=passed,
        scenario_verdicts=tuple(verdicts),
        p99_latency_us=None,
        p99_latency_us_llm=None,
    )


class TestAggregateNSweepReports:
    def test_all_pass_in_all_sweeps(self) -> None:
        """A scenario that passes in all 3 sweeps produces (3, 3)."""
        reports = [
            _make_report([_make_verdict("A", passed=True)]),
            _make_report([_make_verdict("A", passed=True)]),
            _make_report([_make_verdict("A", passed=True)]),
        ]
        result = _aggregate_n_sweep_reports(reports)
        assert result == {"A": (3, 3)}

    def test_mixed_passes(self) -> None:
        """Known mix: A passes all, B passes 2/3, C fails all."""
        reports = [
            _make_report(
                [
                    _make_verdict("A", passed=True),
                    _make_verdict("B", passed=True),
                    _make_verdict("C", passed=False),
                ]
            ),
            _make_report(
                [
                    _make_verdict("A", passed=True),
                    _make_verdict("B", passed=True),
                    _make_verdict("C", passed=False),
                ]
            ),
            _make_report(
                [
                    _make_verdict("A", passed=True),
                    _make_verdict("B", passed=False),
                    _make_verdict("C", passed=False),
                ]
            ),
        ]
        result = _aggregate_n_sweep_reports(reports)
        assert result == {"A": (3, 3), "B": (2, 3), "C": (0, 3)}

    def test_single_report(self) -> None:
        """Single report produces (pass_count, 1) per scenario."""
        reports = [
            _make_report(
                [
                    _make_verdict("X", passed=True),
                    _make_verdict("Y", passed=False),
                ]
            )
        ]
        result = _aggregate_n_sweep_reports(reports)
        assert result == {"X": (1, 1), "Y": (0, 1)}

    def test_scenario_set_mismatch_raises(self) -> None:
        """Different scenario sets across reports raise ValueError."""
        report_a = _make_report([_make_verdict("A", passed=True)])
        report_b = _make_report([_make_verdict("B", passed=True)])
        with pytest.raises(ValueError, match="scenario.*mismatch|corpus.*stability|differ"):
            _aggregate_n_sweep_reports([report_a, report_b])

    def test_aggregate_empty_reports_returns_empty_dict(self) -> None:
        """_aggregate_n_sweep_reports([]) returns an empty dict."""
        result = _aggregate_n_sweep_reports([])
        assert result == {}
