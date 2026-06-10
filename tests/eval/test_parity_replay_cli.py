"""Parity-replay CLI surface.

Exercises arg parsing, subset filtering, output-path defaulting, and the
plumb-through to `replay()` + `write_parity_report`. The CLI mocks
`replay()` so the test doesn't need a live LLM or the private samantha
corpus on the test host.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest


def _mock_report() -> object:
    """A minimal AccuracyReport stand-in for CLI plumb-through tests."""
    from samantha_server.scenarios.replay import (
        AccuracyReport,
        ExpectedOutput,
        PredictedOutput,
        ScenarioVerdict,
        StepVerdict,
    )

    sv = StepVerdict(
        scenario_id="SC-001",
        step_index=1,
        status="pass",
        expected=ExpectedOutput(next_state="ACCEPTED", applied_rules=("ACC-008",), flags=()),
        predicted=PredictedOutput(next_state="ACCEPTED", applied_rules=("ACC-008",), flags=()),
        latency_us=1_000_000,
        routing_path="deterministic",
    )
    verdict = ScenarioVerdict(
        scenario_id="SC-001",
        category="rule_coverage",
        status="pass",
        step_verdicts=(sv,),
    )
    return AccuracyReport(
        included_accuracy=1.0,
        overall_accuracy=1.0,
        included_total=1,
        overall_total=1,
        included_pass=1,
        overall_pass=1,
        scenario_verdicts=(verdict,),
        p99_latency_us=1_000_000,
        p99_latency_us_llm=None,
        deterministic_latency_step_count=1,
        llm_latency_step_count=0,
    )


def test_cli_screening_subset_passes_pinned_ids(tmp_path: Path) -> None:
    """`--subset screening` plumbs the pinned 33-fixture allow-list through
    `replay(include_scenario_ids=…)`."""
    from samantha_server.eval.parity_replay import main
    from samantha_server.eval.screening import SCREENING_SCENARIO_IDS

    captured: dict[str, object] = {}

    def fake_replay(directory: Path, **kwargs: object) -> object:
        captured["directory"] = directory
        captured["kwargs"] = kwargs
        return _mock_report()

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    with patch("samantha_server.eval.parity_replay.replay", side_effect=fake_replay):
        exit_code = main(
            [
                "--subset",
                "screening",
                "--corpus",
                str(corpus),
                "--out",
                str(tmp_path / "out"),
            ]
        )

    assert exit_code == 0
    assert captured["kwargs"]["include_scenario_ids"] == SCREENING_SCENARIO_IDS  # type: ignore[index]
    # receipts_db_path must be set (parity isolation), pointing at a fresh path.
    assert captured["kwargs"]["receipts_db_path"] is not None  # type: ignore[index]


def test_cli_accumulated_state_subset_no_filter(tmp_path: Path) -> None:
    """`--subset accumulated_state` runs the full `accumulated_state/` directory
    (no scenario_id filter — the directory itself is the subset)."""
    from samantha_server.eval.parity_replay import main

    captured: dict[str, object] = {}

    def fake_replay(directory: Path, **kwargs: object) -> object:
        captured["directory"] = directory
        captured["kwargs"] = kwargs
        return _mock_report()

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    out = tmp_path / "out"
    with patch("samantha_server.eval.parity_replay.replay", side_effect=fake_replay):
        exit_code = main(
            ["--subset", "accumulated_state", "--corpus", str(corpus), "--out", str(out)]
        )

    assert exit_code == 0
    # accumulated_state subset routes the directory to the corpus's
    # accumulated_state/ subdirectory; no include_scenario_ids filter.
    assert captured["directory"] == corpus / "accumulated_state"  # type: ignore[index]
    assert captured["kwargs"]["include_scenario_ids"] is None  # type: ignore[index]


def test_cli_writes_parity_report_artifact(tmp_path: Path) -> None:
    """The CLI emits `<out>/<run_id>.json` after replay completes."""
    from samantha_server.eval.parity_replay import main

    out = tmp_path / "out"
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    with patch("samantha_server.eval.parity_replay.replay", return_value=_mock_report()):
        exit_code = main(
            [
                "--subset",
                "screening",
                "--corpus",
                str(corpus),
                "--out",
                str(out),
                "--run-id",
                "parity-test-001",
            ]
        )

    assert exit_code == 0
    json_path = out / "parity-test-001.json"
    assert json_path.exists(), f"expected JSON artifact at {json_path}"
    payload = json.loads(json_path.read_text())
    assert payload["run_id"] == "parity-test-001"
    assert len(payload["models"]) == 1


def test_cli_corpus_default_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When --corpus is omitted, the CLI honors `SAMANTHA_POC_CORPUS_PATH`."""
    from samantha_server.eval.parity_replay import main

    fake_corpus = tmp_path / "from-env"
    fake_corpus.mkdir()
    monkeypatch.setenv("SAMANTHA_POC_CORPUS_PATH", str(fake_corpus))

    captured: dict[str, object] = {}

    def fake_replay(directory: Path, **kwargs: object) -> object:
        captured["directory"] = directory
        return _mock_report()

    with patch("samantha_server.eval.parity_replay.replay", side_effect=fake_replay):
        exit_code = main(["--subset", "screening", "--out", str(tmp_path / "out")])

    assert exit_code == 0
    # screening subset doesn't change the directory (the filter is by id);
    # accumulated_state would point at <corpus>/accumulated_state.
    assert captured["directory"] == fake_corpus  # type: ignore[index]


def test_cli_default_run_id_format(tmp_path: Path) -> None:
    """When --run-id is omitted the CLI mints a `parity-<ISO>Z-<8hex>` stem
    and writes the artifact under that name."""
    from samantha_server.eval.parity_replay import main

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    out = tmp_path / "out"
    with patch("samantha_server.eval.parity_replay.replay", return_value=_mock_report()):
        exit_code = main(["--subset", "screening", "--corpus", str(corpus), "--out", str(out)])
    assert exit_code == 0
    artifacts = sorted(out.glob("parity-*.json"))
    assert len(artifacts) == 1
    stem = artifacts[0].stem
    assert re.fullmatch(r"parity-\d{8}T\d{6}Z-[0-9a-f]{8}", stem), stem


def test_cli_rejects_traversal_run_id(tmp_path: Path) -> None:
    """`--run-id ../escape` must be rejected before any file is written."""
    from samantha_server.eval.parity_replay import main

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    out = tmp_path / "out"
    with patch("samantha_server.eval.parity_replay.replay", return_value=_mock_report()):
        exit_code = main(
            [
                "--subset",
                "screening",
                "--corpus",
                str(corpus),
                "--out",
                str(out),
                "--run-id",
                "../escape",
            ]
        )
    assert exit_code == 1
    # No JSON written anywhere under tmp_path (especially not the parent).
    assert list(tmp_path.rglob("*.json")) == []


def test_cli_missing_corpus_returns_one(tmp_path: Path) -> None:
    """A missing corpus directory exits with a one-line message, not a traceback."""
    from samantha_server.eval.parity_replay import main

    missing = tmp_path / "does-not-exist"
    out = tmp_path / "out"
    with patch("samantha_server.eval.parity_replay.replay", return_value=_mock_report()):
        exit_code = main(["--subset", "screening", "--corpus", str(missing), "--out", str(out)])
    assert exit_code == 1


def test_cli_empty_report_refused(tmp_path: Path) -> None:
    """A replay that yields zero scenarios (empty corpus / fully-mismatched
    filter) must refuse to emit a green report."""
    from samantha_server.eval.parity_replay import main
    from samantha_server.scenarios.replay import AccuracyReport

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    out = tmp_path / "out"
    empty_report = AccuracyReport(
        included_accuracy=1.0,
        overall_accuracy=1.0,
        included_total=0,
        overall_total=0,
        included_pass=0,
        overall_pass=0,
        scenario_verdicts=(),
        p99_latency_us=None,
        p99_latency_us_llm=None,
        deterministic_latency_step_count=0,
        llm_latency_step_count=0,
    )
    with patch("samantha_server.eval.parity_replay.replay", return_value=empty_report):
        exit_code = main(["--subset", "screening", "--corpus", str(corpus), "--out", str(out)])
    assert exit_code == 1
    assert list(out.glob("*.json")) == []


def test_cli_persists_receipts_db_alongside_artifacts(tmp_path: Path) -> None:
    """Receipts DB lives at `<out>/<run_id>.receipts.sqlite` so the audit
    trail outlives the CLI invocation."""
    from samantha_server.eval.parity_replay import main

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    out = tmp_path / "out"
    captured: dict[str, object] = {}

    def fake_replay(directory: Path, **kwargs: object) -> object:
        captured["receipts_db_path"] = kwargs["receipts_db_path"]
        # Touch the file so the test can assert it survives the CLI exit.
        Path(kwargs["receipts_db_path"]).write_bytes(b"")  # type: ignore[arg-type]
        return _mock_report()

    with patch("samantha_server.eval.parity_replay.replay", side_effect=fake_replay):
        exit_code = main(
            [
                "--subset",
                "screening",
                "--corpus",
                str(corpus),
                "--out",
                str(out),
                "--run-id",
                "parity-persist-001",
            ]
        )
    assert exit_code == 0
    receipts_path = out / "parity-persist-001.receipts.sqlite"
    assert receipts_path.exists()
    assert captured["receipts_db_path"] == receipts_path
