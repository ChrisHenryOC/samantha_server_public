"""Tests for samantha_server.scenarios CLI entry point."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

VENDORED_DIR = Path(__file__).parent.parent / "fixtures" / "scenarios"

_PASSING_SCENARIO = {
    "scenario_id": "SC-CLI-PASS",
    "category": "rule_coverage",
    "description": "Deterministic pass — no LLM required",
    "events": [
        {
            "step": 1,
            "event_type": "order_received",
            "event_data": {
                "patient_name": "TEST, CLI",
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


def _write_deterministic_corpus(tmp_path: Path) -> Path:
    """Write a minimal deterministic corpus to tmp_path and return the path."""
    subdir = tmp_path / "rule_coverage"
    subdir.mkdir(parents=True, exist_ok=True)
    (subdir / "sc-cli-pass.json").write_text(json.dumps(_PASSING_SCENARIO, indent=2))
    return tmp_path


def test_cli_exits_0_on_passing_corpus(tmp_path: Path) -> None:
    """python -m samantha_server.scenarios.replay <dir> exits 0 when accuracy >= 0.995.

    Uses a deterministic-only corpus so the CLI does not attempt to load MLX.
    LLM-path scenarios in the vendored corpus require MLX which is not available in CI.
    """
    corpus_dir = _write_deterministic_corpus(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "samantha_server.scenarios.replay", str(corpus_dir)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"CLI exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Included accuracy" in result.stdout
    assert "PASS" in result.stdout


def test_cli_prints_report_to_stdout(tmp_path: Path) -> None:
    """Uses a deterministic-only corpus so the CLI does not attempt to load MLX."""
    corpus_dir = _write_deterministic_corpus(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "samantha_server.scenarios.replay", str(corpus_dir)],
        capture_output=True,
        text=True,
    )
    assert "Overall accuracy" in result.stdout
    assert "Scenario Replay Report" in result.stdout


def test_cli_prints_skipped_line_when_skiplist_non_empty(tmp_path: Path) -> None:
    """CLI must print 'Skipped' summary line when skiplist has entries."""
    import json

    subdir = tmp_path / "rule_coverage"
    subdir.mkdir()
    scenario = {
        "scenario_id": "SC-SKIP-TEST",
        "category": "rule_coverage",
        "description": "Skiplist test",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Z",
                    "age": 50,
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
    (subdir / "sc-skip-test.json").write_text(json.dumps(scenario, indent=2))
    # Add it to skiplist
    (tmp_path / ".skiplist.json").write_text(
        json.dumps(
            {
                "_comment": "test",
                "skipped": {
                    "SC-SKIP-TEST": "https://github.com/ChrisHenryOC/samantha_server/issues/99"
                },
            }
        )
    )

    result = subprocess.run(
        [sys.executable, "-m", "samantha_server.scenarios.replay", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert "Skipped" in result.stdout, f"Expected 'Skipped' in stdout:\n{result.stdout}"
    assert "SC-SKIP-TEST" in result.stdout


def test_cli_stamp_prompt_flag_accepted(tmp_path: Path) -> None:
    """GH-196 Slice 3: --stamp-prompt is accepted and does not cause an error.

    Uses a deterministic-only corpus so no LLM is loaded. The flag
    sets SAMANTHA_STAMP_PROMPT=1 in the subprocess environment; the
    test just checks that the CLI doesn't error on the flag itself.
    """
    corpus_dir = _write_deterministic_corpus(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "samantha_server.scenarios.replay",
            str(corpus_dir),
            "--stamp-prompt",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"CLI exited {result.returncode} with --stamp-prompt\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_cli_exits_1_on_failing_corpus(tmp_path: Path) -> None:
    """CLI exits 1 when included_accuracy < 0.995."""
    import json

    subdir = tmp_path / "rule_coverage"
    subdir.mkdir()
    failing = {
        "scenario_id": "SC-FAIL",
        "category": "rule_coverage",
        "description": "Intentional fail",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, Z",
                    "age": 50,
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
                    "next_state": "DO_NOT_PROCESS",  # wrong — engine returns ACCEPTED
                    "applied_rules": [],
                    "flags": [],
                    "routing_path": "deterministic",
                },
            }
        ],
    }
    (subdir / "sc-fail.json").write_text(json.dumps(failing, indent=2))

    result = subprocess.run(
        [sys.executable, "-m", "samantha_server.scenarios.replay", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
