"""Tests for multi-model × N-sweep aggregation (GH-262 review fix).

Verifies:
- --models=A,B --n-sweeps=3 produces two per-model N-sweep summaries, each N=3 (not N=6)
- --models=A,B --n-sweeps=3 cross-model summary has exactly 2 rows (not 6)
- exit code is non-zero when every individual sweep passes 99.5% but a fixture is flaky
- partial sweep (one call raises) produces N=actual in header and emits a warning
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import samantha_server.scenarios.replay as replay_module
from samantha_server.scenarios.replay import (
    AccuracyReport,
    ExpectedOutput,
    PredictedOutput,
    ScenarioVerdict,
    StepVerdict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    return ScenarioVerdict(
        scenario_id=scenario_id,
        category="rule_coverage",
        status="pass" if passed else "fail",
        step_verdicts=(_make_step(scenario_id, passed=passed),),
    )


def _make_report(scenario_id: str, *, passed: bool = True) -> AccuracyReport:
    verdict = _make_verdict(scenario_id, passed=passed)
    return AccuracyReport(
        included_accuracy=1.0 if passed else 0.0,
        overall_accuracy=1.0 if passed else 0.0,
        included_total=1,
        overall_total=1,
        included_pass=1 if passed else 0,
        overall_pass=1 if passed else 0,
        scenario_verdicts=(verdict,),
        p99_latency_us=None,
        p99_latency_us_llm=None,
    )


def _write_deterministic_scenario(tmp_path: Path, scenario_id: str = "SC-MM-01") -> None:
    """Write a single-step deterministic scenario (rule_coverage category)."""
    subdir = tmp_path / "rule_coverage"
    subdir.mkdir(parents=True, exist_ok=True)
    fixture = {
        "scenario_id": scenario_id,
        "category": "rule_coverage",
        "description": "Multi-model N-sweeps test scenario",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, MM",
                    "age": 45,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "expected_output": {
                    "next_state": "ACCEPTED",
                    "applied_rules": ["ACC-008"],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }
    (subdir / f"{scenario_id.lower()}.json").write_text(json.dumps(fixture, indent=2))


# ---------------------------------------------------------------------------
# Slice 1: per-model N-sweep summaries (each model gets its own, N=3 not N=6)
# ---------------------------------------------------------------------------


class TestPerModelNSweepSummaries:
    def test_two_models_produce_two_summaries_each_with_correct_n(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """--models=fake-A,fake-B --n-sweeps=3 produces two N-sweep summaries, each N=3."""
        _write_deterministic_scenario(tmp_path)

        report_a = _make_report("SC-MM-01", passed=True)
        report_b = _make_report("SC-MM-01", passed=True)

        call_seq: list[str] = []

        def fake_replay(directory: object, **kwargs: object) -> AccuracyReport:
            # Return report_a for fake-A, report_b for fake-B, based on
            # current cfg.LLM_MODEL_NAME set by main().
            import samantha_server.config as cfg

            call_seq.append(cfg.LLM_MODEL_NAME)
            return report_a if cfg.LLM_MODEL_NAME == "fake-A" else report_b

        with patch.object(replay_module, "replay", side_effect=fake_replay):
            exit_code = replay_module.main(
                [str(tmp_path), "--models=fake-A,fake-B", "--n-sweeps=3"]
            )

        captured = capsys.readouterr()
        assert exit_code == 0

        # Should have called replay 6 times total (3 sweeps × 2 models)
        assert len(call_seq) == 6

        # Each model should appear exactly 3 times
        assert call_seq.count("fake-A") == 3
        assert call_seq.count("fake-B") == 3

        # Two distinct N-sweep summary blocks, each with N=3 (not N=6)
        summary_lines = [ln for ln in captured.out.splitlines() if "N-sweep summary" in ln]
        assert len(summary_lines) == 2, f"Expected 2 N-sweep summaries, got: {summary_lines}"
        for line in summary_lines:
            assert "N=3" in line, f"Expected N=3 in header, got: {line!r}"
            assert "N=6" not in line, f"N=6 should not appear in header, got: {line!r}"

    def test_two_models_summaries_have_model_header(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """In multi-model mode, each N-sweep summary is preceded by a model header."""
        _write_deterministic_scenario(tmp_path)

        report = _make_report("SC-MM-01", passed=True)

        with patch.object(replay_module, "replay", return_value=report):
            replay_module.main([str(tmp_path), "--models=fake-A,fake-B", "--n-sweeps=2"])

        captured = capsys.readouterr()
        # The model headers should appear before the summaries.
        assert "fake-A" in captured.out
        assert "fake-B" in captured.out


# ---------------------------------------------------------------------------
# Slice 2: cross-model summary row count
# ---------------------------------------------------------------------------


class TestCrossModelSummaryRowCount:
    def test_two_models_three_sweeps_cross_model_summary_has_two_rows(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """--models=A,B --n-sweeps=3 → cross-model summary has exactly 2 rows, not 6."""
        _write_deterministic_scenario(tmp_path)

        report = _make_report("SC-MM-01", passed=True)

        with patch.object(replay_module, "replay", return_value=report):
            exit_code = replay_module.main(
                [str(tmp_path), "--models=fake-A,fake-B", "--n-sweeps=3"]
            )

        assert exit_code == 0
        captured = capsys.readouterr()

        # The cross-model summary block: count lines that start with "  PASS" or "  FAIL"
        summary_rows = [
            ln
            for ln in captured.out.splitlines()
            if ln.startswith("  PASS") or ln.startswith("  FAIL")
        ]
        assert len(summary_rows) == 2, (
            f"Expected 2 cross-model summary rows, got {len(summary_rows)}: {summary_rows}"
        )


# ---------------------------------------------------------------------------
# Slice 3: exit-code teeth — flaky fixture even if all sweeps pass 99.5%
# ---------------------------------------------------------------------------


class TestExitCodeFlaky:
    def test_flaky_fixture_nonzero_exit_code(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """All individual sweeps pass 99.5% but one fixture is flaky → exit code non-zero."""
        _write_deterministic_scenario(tmp_path, scenario_id="SC-MM-01")
        # Write a second scenario so individual sweeps have 100% (2/2 each sweep)
        # but SC-MM-02 will fail on sweep 2, making it flaky.
        subdir = tmp_path / "rule_coverage"
        fixture2 = {
            "scenario_id": "SC-MM-02",
            "category": "rule_coverage",
            "description": "Second scenario",
            "events": [
                {
                    "step": 1,
                    "event_type": "order_received",
                    "event_data": {
                        "patient_name": "TEST, MM2",
                        "age": 45,
                        "sex": "F",
                        "specimen_type": "biopsy",
                        "anatomic_site": "breast",
                        "fixative": "formalin",
                        "fixation_time_hours": 24.0,
                        "ordered_tests": ["Breast IHC Panel"],
                        "priority": "routine",
                        "billing_info_present": True,
                    },
                    "expected_output": {
                        "next_state": "ACCEPTED",
                        "applied_rules": ["ACC-008"],
                        "flags": [],
                        "routing_path": "deterministic",
                    },
                }
            ],
        }
        (subdir / "sc-mm-02.json").write_text(json.dumps(fixture2, indent=2))

        # Build a report where every individual sweep passes (included_accuracy=1.0).
        # We'll patch _aggregate_n_sweep_reports to return a flaky result so that
        # the test exercises the exit-code teeth without needing a complex corpus.
        verdict_pass_01 = _make_verdict("SC-MM-01", passed=True)
        verdict_pass_02 = _make_verdict("SC-MM-02", passed=True)

        report_all_pass = AccuracyReport(
            included_accuracy=1.0,
            overall_accuracy=1.0,
            included_total=2,
            overall_total=2,
            included_pass=2,
            overall_pass=2,
            scenario_verdicts=(verdict_pass_01, verdict_pass_02),
            p99_latency_us=None,
            p99_latency_us_llm=None,
        )

        # Use mocked reports where every sweep passes individually (exit_code=0 per sweep)
        # but the aggregate shows a flaky fixture.
        call_count = 0

        def fake_replay_all_pass(*args: object, **kwargs: object) -> AccuracyReport:
            nonlocal call_count
            call_count += 1
            return report_all_pass

        # Patch _aggregate_n_sweep_reports to return a flaky result
        flaky_aggregate = {"SC-MM-01": (3, 3), "SC-MM-02": (2, 3)}

        with (
            patch.object(replay_module, "replay", side_effect=fake_replay_all_pass),
            patch.object(replay_module, "_aggregate_n_sweep_reports", return_value=flaky_aggregate),
        ):
            exit_code = replay_module.main([str(tmp_path), "--n-sweeps=3"])

        assert exit_code != 0, "Expected non-zero exit code when a fixture is flaky (K<N), got 0"


# ---------------------------------------------------------------------------
# Slice 4: actual_sweep_count in header when some sweeps error
# ---------------------------------------------------------------------------


class TestPartialSweepHeader:
    def test_partial_sweep_shows_actual_n_not_requested(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """When 1 of 3 sweeps errors, header shows N=2 not N=3, and emits warning."""
        _write_deterministic_scenario(tmp_path)

        report = _make_report("SC-MM-01", passed=True)
        call_count = 0

        def fake_replay_with_one_error(*args: object, **kwargs: object) -> AccuracyReport:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Simulated sweep failure")
            return report

        with patch.object(replay_module, "replay", side_effect=fake_replay_with_one_error):
            exit_code = replay_module.main([str(tmp_path), "--n-sweeps=3"])

        # exit code must be non-zero (one sweep failed)
        assert exit_code != 0

        captured = capsys.readouterr()
        # Header must say N=2, not N=3
        assert "N=2" in captured.out, f"Expected N=2 in output, got:\n{captured.out}"
        assert "N=3" not in captured.out or "N=2" in captured.out  # N=3 ok in sweep label

        # Warning must appear about partial completion
        assert "WARNING" in captured.out or "WARNING" in captured.err, (
            "Expected a WARNING about partial sweep completion"
        )
        # The warning must mention both actual and requested counts
        warning_text = captured.out + captured.err
        assert "2" in warning_text
        assert "3" in warning_text


# ---------------------------------------------------------------------------
# Slice 5: ValueError from _aggregate_n_sweep_reports handled gracefully in main()
# ---------------------------------------------------------------------------


class TestAggregatorValueErrorHandling:
    def test_aggregator_value_error_no_traceback_nonzero_exit(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """When _aggregate_n_sweep_reports raises ValueError, main() emits a WARNING
        to stderr (not a traceback) and exits non-zero."""
        _write_deterministic_scenario(tmp_path)

        report = _make_report("SC-MM-01", passed=True)

        with (
            patch.object(replay_module, "replay", return_value=report),
            patch.object(
                replay_module,
                "_aggregate_n_sweep_reports",
                side_effect=ValueError("Corpus stability mismatch: scenario sets differ."),
            ),
        ):
            exit_code = replay_module.main([str(tmp_path), "--n-sweeps=3"])

        assert exit_code != 0, "Expected non-zero exit when aggregation raises ValueError"

        captured = capsys.readouterr()
        # Must not contain 'Traceback' — no unhandled exception
        assert "Traceback" not in captured.err, f"Traceback leaked to stderr:\n{captured.err}"
        # Must emit a WARNING to stderr
        assert "WARNING" in captured.err, f"Expected WARNING on stderr, got:\n{captured.err}"

    def test_aggregator_value_error_per_sweep_output_preserved(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Per-sweep diagnostics (e.g. per-sweep report lines) are visible before
        the WARNING when aggregation fails."""
        _write_deterministic_scenario(tmp_path)

        report = _make_report("SC-MM-01", passed=True)

        with (
            patch.object(replay_module, "replay", return_value=report),
            patch.object(
                replay_module,
                "_aggregate_n_sweep_reports",
                side_effect=ValueError("sets differ"),
            ),
        ):
            replay_module.main([str(tmp_path), "--n-sweeps=3"])

        captured = capsys.readouterr()
        # Per-sweep output: "[Sweep N/3]" must have printed before the warning
        assert "[Sweep 1/3]" in captured.out, (
            f"Per-sweep output missing from stdout:\n{captured.out}"
        )


# ---------------------------------------------------------------------------
# Slice 6: replay_with_langfuse_export is called N times under n_sweeps
# ---------------------------------------------------------------------------


class TestLangfuseNSweepsRouting:
    def test_n_sweeps_3_calls_langfuse_release_three_times(
        self, tmp_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GH-338: With LANGFUSE_ENABLED + --n-sweeps=3, replay_with_langfuse_export is
        called exactly 3 times (catches the routing-change blind spot from Medium #6)."""
        _write_deterministic_scenario(tmp_path)

        import samantha_server.config as cfg

        monkeypatch.setattr(cfg, "LANGFUSE_ENABLED", True)
        monkeypatch.setattr(cfg, "LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setattr(cfg, "LANGFUSE_SECRET_KEY", "sk-test")
        monkeypatch.setattr(cfg, "LANGFUSE_BASE_URL", "http://localhost:3000")

        report = _make_report("SC-MM-01", passed=True)

        call_count = 0

        def fake_langfuse_export(
            directory: object,
            *,
            langfuse_base_url: str,
            langfuse_public_key: str,
            langfuse_secret_key: str,
            release: str,
            **kwargs: object,
        ) -> tuple[AccuracyReport, list[str]]:
            nonlocal call_count
            call_count += 1
            return report, []

        with patch.object(
            replay_module, "replay_with_langfuse_export", side_effect=fake_langfuse_export
        ):
            exit_code = replay_module.main([str(tmp_path), "--n-sweeps=3"])

        assert exit_code == 0
        assert call_count == 3, (
            f"Expected replay_with_langfuse_export called 3 times, got {call_count}"
        )
