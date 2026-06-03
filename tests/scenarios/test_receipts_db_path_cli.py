"""Tests for --receipts-db-path CLI argument (GH-306).

Verifies:
- --receipts-db-path=PATH parses to args.receipts_db_path == Path(PATH)
- Default value is None
- --help output contains --receipts-db-path
- main() with --receipts-db-path creates a SQLite DB at that path with the
  receipts schema (deterministic-only corpora produce a schema-only DB by
  design — see GH-156 design note in replay.py)
- PR315 review #1+#8+#9: argparse validator rejects unwritable parent dirs
  at parse time (one error, not N × sweep × model tracebacks)
- PR315 review #7: main() refuses --receipts-db-path that aliases the
  production cfg.RECEIPTS_DB_PATH
- PR315 review #3+#6: main() with --models splits per-model receipts files
  and the multi-model Langfuse path forwards the per-model derived path
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest


def _build_parser() -> argparse.ArgumentParser:
    from samantha_server.scenarios.replay import _build_replay_parser

    return _build_replay_parser()


def _all_pass_scenario(scenario_id: str = "SC-RDB-CLI-01") -> dict[str, Any]:
    """Minimal deterministic scenario that passes the rule engine."""
    return {
        "scenario_id": scenario_id,
        "category": "rule_coverage",
        "description": f"CLI receipts-db-path test fixture {scenario_id}",
        "events": [
            {
                "step": 1,
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TEST, ReceiptsDB",
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


def _write(tmp_path: Path, scenario: dict[str, Any], category: str = "rule_coverage") -> Path:
    sub = tmp_path / category
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / f"{scenario['scenario_id'].lower()}.json"
    path.write_text(json.dumps(scenario))
    return path


# ---------------------------------------------------------------------------
# Slice 1 — parser tests
# ---------------------------------------------------------------------------


def test_receipts_db_path_arg_parses_to_resolved_path(tmp_path: Path) -> None:
    """--receipts-db-path resolves the input via Path.resolve()."""
    parser = _build_parser()
    raw = tmp_path / "foo.db"
    args = parser.parse_args([f"--receipts-db-path={raw}", "/some/dir"])
    assert args.receipts_db_path == raw.resolve()


def test_receipts_db_path_default_is_none() -> None:
    """When --receipts-db-path is omitted, args.receipts_db_path defaults to None."""
    parser = _build_parser()
    args = parser.parse_args(["/some/dir"])
    assert args.receipts_db_path is None


def test_receipts_db_path_appears_in_help(capsys: pytest.CaptureFixture[str]) -> None:
    """--help output contains --receipts-db-path."""
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    captured = capsys.readouterr()
    assert "--receipts-db-path" in captured.out


def test_receipts_db_path_validator_creates_missing_parent_dir(tmp_path: Path) -> None:
    """PR315 review #1+#9: validator mkdirs the parent so a deep path Just Works.

    Before this fix, a non-existent parent raised ``sqlite3.OperationalError``
    from inside the sweep loop and was swallowed by the per-model except guard.
    """
    parser = _build_parser()
    nested = tmp_path / "deep" / "nested" / "receipts.db"
    args = parser.parse_args([f"--receipts-db-path={nested}", "/some/dir"])
    assert args.receipts_db_path == nested.resolve()
    assert nested.parent.is_dir(), "validator must mkdir parents=True"


def test_receipts_db_path_validator_rejects_unwritable_parent(tmp_path: Path) -> None:
    """PR315 review #1+#9: validator raises ArgumentTypeError on permission failure.

    Bypasses the swallowed-OperationalError-in-sweep-loop bug. Uses a
    read-only parent dir to provoke a real OSError from ``mkdir``.
    """
    parser = _build_parser()
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)  # r-x: cannot create children
    try:
        with pytest.raises(SystemExit):
            parser.parse_args([f"--receipts-db-path={locked / 'child' / 'r.db'}", "/some/dir"])
    finally:
        locked.chmod(0o700)  # restore so pytest can clean up


# ---------------------------------------------------------------------------
# Slice 2 — end-to-end main() threading
# ---------------------------------------------------------------------------


def test_main_creates_receipts_db_at_supplied_path(tmp_path: Path) -> None:
    """main() with --receipts-db-path persists a SQLite receipts DB at that path.

    The fixture uses ``category: "rule_coverage"`` (deterministic-only), so
    by GH-156 design replay() takes the ``_init_receipts_schema`` branch and
    writes the schema without constructing _ReplayDeps. The DB is therefore
    schema-populated but row-empty by design; assert specifically the
    ``receipts`` table exists rather than "any table".

    Row-population coverage on the LLM-path branch is deferred to a
    follow-up; building a stub-LLM scenario that exercises dispatch_event
    end-to-end is out of scope for this fix-review pass.
    """
    import samantha_server.config as cfg
    from samantha_server.scenarios.replay import main

    _write(tmp_path, _all_pass_scenario("SC-RDB-CLI-02"))
    db_path = (tmp_path / "cli-receipts.db").resolve()

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(cfg, "LANGFUSE_ENABLED", False)
        exit_code = main([f"--receipts-db-path={db_path}", str(tmp_path)])

    assert exit_code == 0
    assert db_path.exists(), (
        f"GH-306: receipts DB must exist at {db_path} after main() -- "
        "the flag was not forwarded to replay()."
    )
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
    finally:
        conn.close()
    assert "receipts" in tables, (
        f"GH-306: receipts DB at {db_path} missing 'receipts' table. Got tables={tables!r}"
    )


# ---------------------------------------------------------------------------
# Slice 3 — prod-path guard (PR315 review #7)
# ---------------------------------------------------------------------------


def test_main_refuses_receipts_db_path_pointing_at_prod_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """PR315 review #7: --receipts-db-path == cfg.RECEIPTS_DB_PATH must error out.

    Synthetic replay receipts would contaminate the prod audit trail; the
    schema is idempotent but the data model is not (different sweep IDs,
    different signing keys in dev vs prod).
    """
    import samantha_server.config as cfg
    from samantha_server.scenarios.replay import main

    _write(tmp_path, _all_pass_scenario("SC-RDB-CLI-PROD"))
    prod_target = tmp_path / "fake-prod-receipts.db"

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(cfg, "RECEIPTS_DB_PATH", str(prod_target))
        mp.setattr(cfg, "LANGFUSE_ENABLED", False)
        exit_code = main([f"--receipts-db-path={prod_target}", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 1, f"expected refusal exit code 1, got {exit_code}"
    assert "production receipts store" in captured.err, (
        f"expected refusal message in stderr; got: {captured.err!r}"
    )
    assert not prod_target.exists(), "main() must short-circuit before opening the prod path"


# ---------------------------------------------------------------------------
# Slice 4 — multi-model per-model split (PR315 review #3 + #6)
# ---------------------------------------------------------------------------


def test_main_splits_receipts_db_path_per_model_under_multi_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR315 review #6: --models A,B --receipts-db-path foo.db → per-model files.

    Without the split, both models' receipts append to the same file with no
    model_id discriminator column; the GH-311 chart consumer would have to
    demux via Langfuse trace_id (only available when Langfuse was enabled).
    Also covers PR315 review #3: the multi-model Langfuse path forwards the
    derived per-model path.
    """
    import samantha_server.config as cfg
    from samantha_server.scenarios.replay import main as replay_main

    _write(tmp_path, _all_pass_scenario("SC-RDB-CLI-MM"))
    base_path = (tmp_path / "mm-receipts.db").resolve()

    calls: list[dict[str, Any]] = []

    def fake_with_release(
        corpus_dir: Path,
        *,
        receipts_db_path: Path | None = None,
        release: str = "",
        **kwargs: Any,
    ) -> tuple[Any, list[str]]:
        from samantha_server.scenarios.replay import AccuracyReport

        calls.append({"release": release, "receipts_db_path": receipts_db_path})
        return (
            AccuracyReport(
                included_accuracy=1.0,
                overall_accuracy=1.0,
                included_total=1,
                overall_total=1,
                included_pass=1,
                overall_pass=1,
                scenario_verdicts=(),
                p99_latency_us=500,
                p99_latency_us_llm=None,
                deterministic_latency_step_count=1,
                llm_latency_step_count=0,
            ),
            [],
        )

    monkeypatch.setattr(cfg, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(cfg, "LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(cfg, "LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(cfg, "LANGFUSE_BASE_URL", "http://localhost:3000")
    from samantha_server.scenarios import replay as replay_module

    monkeypatch.setattr(replay_module, "replay_with_langfuse_export", fake_with_release)
    monkeypatch.setattr(replay_module, "_warm_up_model", lambda *_a, **_k: None)
    # _model_scope mutates cfg.LLM_MODEL_NAME; skip the real implementation so
    # we don't depend on model-path validity in the test environment.
    from contextlib import contextmanager

    @contextmanager
    def fake_scope(_model_id: str) -> Any:
        yield

    monkeypatch.setattr(replay_module, "_model_scope", fake_scope)

    exit_code = replay_main(
        [
            "--models=model-A,model-B/quant:q4",
            f"--receipts-db-path={base_path}",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert len(calls) == 2, f"expected one call per model, got {len(calls)}"
    paths = [c["receipts_db_path"] for c in calls]
    # Each model gets a per-model file; ``with_stem`` preserves the suffix.
    expected_a = base_path.with_stem(f"{base_path.stem}-model-A")
    expected_b = base_path.with_stem(f"{base_path.stem}-model-B_quant_q4")
    assert expected_a in paths, f"missing per-model path {expected_a}; got {paths}"
    assert expected_b in paths, f"missing per-model path {expected_b}; got {paths}"
    # Forwarding must not collapse to the base path on either model.
    assert base_path not in paths, (
        f"multi-model split lost: base path {base_path} forwarded verbatim; got {paths}"
    )
