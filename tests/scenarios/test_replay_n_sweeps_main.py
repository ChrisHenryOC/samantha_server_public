"""Tests for N-sweep loop wired into main().

Verifies:
- --n-sweeps=3 causes replay() to be called 3 times
- N-sweep summary appears in stdout when n_sweeps > 1
- Default (n_sweeps=1) calls replay exactly once and produces no N-sweep summary
- Regression: no --n-sweeps flag is byte-identical behavior to --n-sweeps=1
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import samantha_server.scenarios.replay as replay_module


def _write_deterministic_scenario(tmp_path: Path, scenario_id: str = "SC-NSW-01") -> None:
    """Write a single-step deterministic scenario (rule_coverage category)."""
    subdir = tmp_path / "rule_coverage"
    subdir.mkdir(parents=True, exist_ok=True)
    fixture = {
        "scenario_id": scenario_id,
        "category": "rule_coverage",
        "description": "N-sweeps test scenario",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, NSW",
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


class TestNSweepsMain:
    def test_n_sweeps_3_calls_replay_three_times(self, tmp_path: Path) -> None:
        """--n-sweeps=3 causes the underlying replay() to be called 3 times."""
        _write_deterministic_scenario(tmp_path)

        real_replay = replay_module.replay
        call_count = 0

        def counting_replay(*args: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            return real_replay(*args, **kwargs)

        with patch.object(replay_module, "replay", side_effect=counting_replay):
            exit_code = replay_module.main([str(tmp_path), "--n-sweeps=3"])

        assert exit_code == 0
        assert call_count == 3

    def test_n_sweeps_summary_appears_when_n_gt_1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """N-sweep summary is printed to stdout when --n-sweeps > 1."""
        _write_deterministic_scenario(tmp_path)

        exit_code = replay_module.main([str(tmp_path), "--n-sweeps=3"])
        assert exit_code == 0

        captured = capsys.readouterr()
        assert "N-sweep summary" in captured.out
        assert "N=3" in captured.out

    def test_default_n_sweeps_calls_replay_exactly_once(self, tmp_path: Path) -> None:
        """When --n-sweeps is omitted, replay() is called exactly once."""
        _write_deterministic_scenario(tmp_path)

        real_replay = replay_module.replay
        call_count = 0

        def counting_replay(*args: object, **kwargs: object) -> object:
            nonlocal call_count
            call_count += 1
            return real_replay(*args, **kwargs)

        with patch.object(replay_module, "replay", side_effect=counting_replay):
            exit_code = replay_module.main([str(tmp_path)])

        assert exit_code == 0
        assert call_count == 1

    def test_n_sweeps_1_no_summary(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """--n-sweeps=1 produces no N-sweep summary (identical to no-flag behavior)."""
        _write_deterministic_scenario(tmp_path)

        exit_code = replay_module.main([str(tmp_path), "--n-sweeps=1"])
        assert exit_code == 0

        captured = capsys.readouterr()
        assert "N-sweep summary" not in captured.out

    def test_n_sweeps_default_no_summary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Default invocation (no --n-sweeps) produces no N-sweep summary."""
        _write_deterministic_scenario(tmp_path)

        exit_code = replay_module.main([str(tmp_path)])
        assert exit_code == 0

        captured = capsys.readouterr()
        assert "N-sweep summary" not in captured.out

    def test_n_sweeps_stable_accuracy_in_summary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """N-sweep summary includes Stable accuracy line."""
        _write_deterministic_scenario(tmp_path)

        exit_code = replay_module.main([str(tmp_path), "--n-sweeps=2"])
        assert exit_code == 0

        captured = capsys.readouterr()
        assert "Stable accuracy:" in captured.out
        assert "Raw accuracy:" in captured.out
