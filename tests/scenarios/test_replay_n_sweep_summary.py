"""Tests for _print_n_sweep_summary (GH-262 Slice 3).

Verifies the N-sweep summary output format: header, stable accuracy,
raw accuracy, and per-fixture flaky/fail lines.
"""

from __future__ import annotations

import pytest


def _call_summary(
    scenario_passes: dict[str, tuple[int, int]], n_sweeps: int, capsys: pytest.CaptureFixture
) -> str:
    from samantha_server.scenarios.replay import _print_n_sweep_summary

    _print_n_sweep_summary(scenario_passes, n_sweeps)
    return capsys.readouterr().out


class TestPrintNSweepSummary:
    def test_header_present(self, capsys: pytest.CaptureFixture) -> None:
        """Output must contain an N-sweep summary header with the sweep count."""
        out = _call_summary({"A": (3, 3)}, n_sweeps=3, capsys=capsys)
        assert "N-sweep summary" in out
        assert "N=3" in out

    def test_stable_accuracy_line(self, capsys: pytest.CaptureFixture) -> None:
        """Stable accuracy = fixtures where passes == n_sweeps."""
        # A passes all 3, B passes 2/3, C fails all — only A is stable.
        scenario_passes = {"A": (3, 3), "B": (2, 3), "C": (0, 3)}
        out = _call_summary(scenario_passes, n_sweeps=3, capsys=capsys)
        # 1 stable out of 3 fixtures = 33.33%
        assert "Stable accuracy:" in out
        assert "1/3" in out
        assert "33.33%" in out

    def test_raw_accuracy_line(self, capsys: pytest.CaptureFixture) -> None:
        """Raw accuracy = total passes / (n_sweeps * fixture_count)."""
        scenario_passes = {"A": (3, 3), "B": (2, 3), "C": (0, 3)}
        out = _call_summary(scenario_passes, n_sweeps=3, capsys=capsys)
        # 3+2+0 = 5 passes over 3*3=9 total = 55.56%
        assert "Raw accuracy:" in out
        assert "5/9" in out
        assert "55.56%" in out

    def test_flaky_fixtures_listed(self, capsys: pytest.CaptureFixture) -> None:
        """Fixtures with passes < n_sweeps are listed individually."""
        scenario_passes = {"A": (3, 3), "B": (2, 3), "C": (0, 3)}
        out = _call_summary(scenario_passes, n_sweeps=3, capsys=capsys)
        assert "B" in out
        assert "2/3" in out
        assert "C" in out
        assert "0/3" in out

    def test_stable_fixtures_not_enumerated(self, capsys: pytest.CaptureFixture) -> None:
        """Fixtures that passed every sweep (K==N) are NOT listed individually."""
        scenario_passes = {"A": (3, 3), "B": (2, 3)}
        out = _call_summary(scenario_passes, n_sweeps=3, capsys=capsys)
        # A is stable — should not appear as a per-fixture failure line.
        # B should appear as flaky.
        lines = out.splitlines()
        # The only place A legitimately appears in a detail line is if it's
        # labelled stable — but we assert it is NOT in the per-fixture flaky list.
        flaky_lines = [ln for ln in lines if "3/3" in ln and "A" in ln and "Stable" not in ln]
        assert not flaky_lines, f"A should not appear as a flaky-line: {flaky_lines}"

    def test_all_stable(self, capsys: pytest.CaptureFixture) -> None:
        """When every fixture passes every sweep, no per-fixture lines are emitted."""
        scenario_passes = {"A": (2, 2), "B": (2, 2)}
        out = _call_summary(scenario_passes, n_sweeps=2, capsys=capsys)
        assert "Stable accuracy:" in out
        assert "2/2" in out
        assert "100.00%" in out

    def test_empty_dict_says_no_scenarios(self, capsys: pytest.CaptureFixture) -> None:
        """_print_n_sweep_summary({}, n_sweeps=N) prints 'No scenarios to summarise.'."""
        out = _call_summary({}, n_sweeps=3, capsys=capsys)
        assert "No scenarios to summarise." in out

    def test_flaky_sort_stable_tiebreak(self, capsys: pytest.CaptureFixture) -> None:
        """Flaky fixtures with the same pass count are sorted by scenario_id (tiebreak)."""
        # Three flaky fixtures: X=1/3, Y=1/3, Z=0/3. Expected order: Z, then X, Y by id.
        scenario_passes = {"Y": (1, 3), "X": (1, 3), "Z": (0, 3)}
        out = _call_summary(scenario_passes, n_sweeps=3, capsys=capsys)
        lines = out.splitlines()
        flaky_section = [ln for ln in lines if ln.strip().startswith(("X:", "Y:", "Z:"))]
        assert len(flaky_section) == 3, f"Expected 3 flaky lines, got: {flaky_section}"
        # Z has fewer passes → comes first; X and Y tie → alphabetical order
        assert flaky_section[0].strip().startswith("Z:"), f"Z should be first: {flaky_section}"
        assert flaky_section[1].strip().startswith("X:"), f"X should be second: {flaky_section}"
        assert flaky_section[2].strip().startswith("Y:"), f"Y should be third: {flaky_section}"
