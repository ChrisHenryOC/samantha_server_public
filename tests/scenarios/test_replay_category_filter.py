"""Tests for the --include-category flag in the replay CLI (GH-184 Slice 5).

Verifies that --include-category=cat1,cat2 filters scenarios to only
run those whose category is in the specified set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_scenario(tmp_path: Path, category: str, scenario_id: str) -> None:
    """Write a minimal passing deterministic scenario."""
    subdir = tmp_path / category
    subdir.mkdir(parents=True, exist_ok=True)
    fixture = {
        "scenario_id": scenario_id,
        "category": category,
        "description": f"{category} scenario {scenario_id}",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Alice",
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
    path = subdir / f"{scenario_id.lower()}.json"
    path.write_text(json.dumps(fixture, indent=2))


# ---------------------------------------------------------------------------
# Tests for replay() include_categories kwarg
# ---------------------------------------------------------------------------


class TestReplayCategoryFilter:
    def test_include_categories_filters_to_specified_set(self, tmp_path: Path) -> None:
        """replay() with include_categories only runs matching scenarios."""
        from samantha_server.scenarios.replay import replay

        _write_scenario(tmp_path, "rule_coverage", "SC-CAT-RC-01")
        _write_scenario(tmp_path, "multi_rule", "SC-CAT-MR-01")
        _write_scenario(tmp_path, "accumulated_state", "SC-CAT-AS-01")

        report = replay(tmp_path, include_categories={"rule_coverage"})

        scenario_ids = {v.scenario_id for v in report.scenario_verdicts}
        assert "SC-CAT-RC-01" in scenario_ids
        assert "SC-CAT-MR-01" not in scenario_ids
        assert "SC-CAT-AS-01" not in scenario_ids

    def test_include_categories_none_runs_all(self, tmp_path: Path) -> None:
        """replay() with include_categories=None runs all scenarios."""
        from samantha_server.scenarios.replay import replay

        _write_scenario(tmp_path, "rule_coverage", "SC-ALL-RC-01")
        _write_scenario(tmp_path, "multi_rule", "SC-ALL-MR-01")

        report = replay(tmp_path, include_categories=None)

        scenario_ids = {v.scenario_id for v in report.scenario_verdicts}
        assert "SC-ALL-RC-01" in scenario_ids
        assert "SC-ALL-MR-01" in scenario_ids

    def test_include_categories_multiple_categories(self, tmp_path: Path) -> None:
        """Multiple categories in the set are all included."""
        from samantha_server.scenarios.replay import replay

        _write_scenario(tmp_path, "rule_coverage", "SC-MULTI-RC-01")
        _write_scenario(tmp_path, "multi_rule", "SC-MULTI-MR-01")
        _write_scenario(tmp_path, "accumulated_state", "SC-MULTI-AS-01")

        report = replay(tmp_path, include_categories={"rule_coverage", "multi_rule"})

        scenario_ids = {v.scenario_id for v in report.scenario_verdicts}
        assert "SC-MULTI-RC-01" in scenario_ids
        assert "SC-MULTI-MR-01" in scenario_ids
        assert "SC-MULTI-AS-01" not in scenario_ids

    def test_include_categories_interacts_with_include_scenario_ids(self, tmp_path: Path) -> None:
        """Both filters apply: scenario must satisfy category AND id filter."""
        from samantha_server.scenarios.replay import replay

        _write_scenario(tmp_path, "rule_coverage", "SC-BOTH-RC-01")
        _write_scenario(tmp_path, "rule_coverage", "SC-BOTH-RC-02")
        _write_scenario(tmp_path, "multi_rule", "SC-BOTH-MR-01")

        # Both category=rule_coverage and scenario_id=SC-BOTH-RC-01 must match.
        report = replay(
            tmp_path,
            include_categories={"rule_coverage"},
            include_scenario_ids={"SC-BOTH-RC-01"},
        )

        scenario_ids = {v.scenario_id for v in report.scenario_verdicts}
        assert "SC-BOTH-RC-01" in scenario_ids
        assert "SC-BOTH-RC-02" not in scenario_ids
        assert "SC-BOTH-MR-01" not in scenario_ids


# ---------------------------------------------------------------------------
# Tests for --include-category CLI flag
# ---------------------------------------------------------------------------


class TestIncludeCategoryCLI:
    def test_cli_include_category_filters_scenarios(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """--include-category=rule_coverage runs only rule_coverage scenarios."""
        from samantha_server.scenarios.replay import main

        _write_scenario(tmp_path, "rule_coverage", "SC-CLI-RC-01")
        _write_scenario(tmp_path, "multi_rule", "SC-CLI-MR-01")

        exit_code = main([str(tmp_path), "--include-category=rule_coverage"])
        assert exit_code == 0

        captured = capsys.readouterr()
        # Only 1 scenario should appear in the report (rule_coverage only)
        assert "1/" in captured.out  # e.g. "1/1" in accuracy line

    def test_cli_include_category_multiple_values(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """--include-category=cat1,cat2 runs scenarios in either category."""
        from samantha_server.scenarios.replay import main

        _write_scenario(tmp_path, "rule_coverage", "SC-CLIMC-RC-01")
        _write_scenario(tmp_path, "multi_rule", "SC-CLIMC-MR-01")
        _write_scenario(tmp_path, "accumulated_state", "SC-CLIMC-AS-01")

        exit_code = main([str(tmp_path), "--include-category=rule_coverage,multi_rule"])
        assert exit_code == 0

        captured = capsys.readouterr()
        # Should have 2 scenarios
        assert "2/" in captured.out

    def test_cli_no_include_category_runs_all(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Without --include-category, all scenarios run."""
        from samantha_server.scenarios.replay import main

        _write_scenario(tmp_path, "rule_coverage", "SC-NOCAT-RC-01")
        _write_scenario(tmp_path, "multi_rule", "SC-NOCAT-MR-01")

        exit_code = main([str(tmp_path)])
        assert exit_code == 0

        captured = capsys.readouterr()
        # Should have 2 scenarios
        assert "2/" in captured.out
