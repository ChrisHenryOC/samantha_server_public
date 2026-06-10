"""Tests for the multi-model comparison reporter.

Test payloads use ``"kind"`` for the discriminated-union field —
matching the production receipt-payload shape (Pydantic serializes
``LLMReviewTrace`` / ``QueryTrace`` etc. with ``kind`` as the
discriminator). The previous fixture-internal ``trace_kind`` was
ahistorical and would have masked a future tightening that filtered
on ``kind``.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

# Anchor the schema path on this file's location so pytest works from
# any cwd. Resolves to <repo>/samantha_server/receipts/schema.sql.
_SCHEMA_PATH: Path = (
    Path(__file__).resolve().parents[2] / "samantha_server" / "receipts" / "schema.sql"
)


def _make_receipts_db(tmp_path: Path, name: str, rows: list[dict[str, Any]]) -> Path:
    """Build a minimal receipts DB at ``tmp_path/name``.

    Schema mirrors ``samantha_server/receipts/schema.sql`` (only the
    columns the comparator reads are populated).
    """
    db_path = tmp_path / name
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA_PATH.read_text())
    cur = conn.cursor()
    for row in rows:
        cur.execute(
            """
            INSERT INTO receipts (
                receipt_id, event_input_hash, applied_rule_id,
                next_state, outcome, signer_key_id, signature,
                payload_json, signed_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["receipt_id"],
                row["event_input_hash"],
                row.get("applied_rule_id"),
                row["next_state"],
                row["outcome"],
                "v1",
                b"\x00" * 64,
                row["payload_json"],
                row.get("signed_at_utc", "2026-05-09T00:00:00+00:00"),
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def test_load_receipts_extracts_model_id_from_payload(tmp_path: Path) -> None:
    """load_receipts reads model_id out of the canonical-JSON payload."""
    from samantha_server.scenarios.compare import load_receipts

    db = _make_receipts_db(
        tmp_path,
        "a.db",
        [
            {
                "receipt_id": "r1",
                "event_input_hash": "h" * 64,
                "applied_rule_id": None,
                "next_state": "ACCESSIONING",
                "outcome": "query_response",
                "payload_json": (
                    '{"decision_traces": [{"kind": "query", '
                    '"model_id": "model-A"}], "outcome": "query_response"}'
                ),
            }
        ],
    )

    rows = load_receipts(db)
    assert len(rows) == 1
    assert rows[0].receipt_id == "r1"
    assert rows[0].event_input_hash == "h" * 64
    assert rows[0].outcome == "query_response"
    assert rows[0].model_id == "model-A"


def test_load_receipts_handles_missing_model_id(tmp_path: Path) -> None:
    """A receipt whose payload has no model_id surfaces ``None`` rather than crashing."""
    from samantha_server.scenarios.compare import load_receipts

    db = _make_receipts_db(
        tmp_path,
        "a.db",
        [
            {
                "receipt_id": "r1",
                "event_input_hash": "h" * 64,
                "applied_rule_id": None,
                "next_state": "ACCESSIONING",
                "outcome": "refused_phi_boundary",
                "payload_json": '{"outcome": "refused_phi_boundary"}',
            }
        ],
    )

    rows = load_receipts(db)
    assert rows[0].model_id is None


def _llm_review_payload(model_id: str) -> str:
    """Helper to keep payload literals short."""
    return f'{{"decision_traces": [{{"kind": "llm_review", "model_id": "{model_id}"}}]}}'


def test_compare_groups_by_event_input_hash(tmp_path: Path) -> None:
    """``compare`` groups receipts across DBs keyed by event_input_hash."""
    from samantha_server.scenarios.compare import compare

    db_a = _make_receipts_db(
        tmp_path,
        "a.db",
        [
            {
                "receipt_id": "ra1",
                "event_input_hash": "scenario1",
                "applied_rule_id": None,
                "next_state": "ACCEPTED",
                "outcome": "accepted_llm_review",
                "payload_json": _llm_review_payload("model-A"),
            },
            {
                "receipt_id": "ra2",
                "event_input_hash": "scenario2",
                "applied_rule_id": None,
                "next_state": "DO_NOT_PROCESS",
                "outcome": "rejected_llm_review",
                "payload_json": _llm_review_payload("model-A"),
            },
        ],
    )
    db_b = _make_receipts_db(
        tmp_path,
        "b.db",
        [
            {
                "receipt_id": "rb1",
                "event_input_hash": "scenario1",
                "applied_rule_id": None,
                "next_state": "ACCEPTED",
                "outcome": "accepted_llm_review",
                "payload_json": _llm_review_payload("model-B"),
            },
            {
                "receipt_id": "rb2",
                "event_input_hash": "scenario2",
                "applied_rule_id": None,
                "next_state": "PENDING_HUMAN_REVIEW",
                "outcome": "escalated_llm_review",
                "payload_json": _llm_review_payload("model-B"),
            },
        ],
    )

    report = compare([db_a, db_b])

    # Two distinct events, each with two model verdicts.
    assert sorted(report.events) == ["scenario1", "scenario2"]
    s1 = report.row("scenario1")
    s2 = report.row("scenario2")

    assert dict(s1.verdicts) == {
        "model-A": "accepted_llm_review",
        "model-B": "accepted_llm_review",
    }
    assert s1.agreement is True

    assert dict(s2.verdicts) == {
        "model-A": "rejected_llm_review",
        "model-B": "escalated_llm_review",
    }
    assert s2.agreement is False


def test_compare_records_models_in_first_seen_order(tmp_path: Path) -> None:
    """The model column order in the report follows the input-DB order."""
    from samantha_server.scenarios.compare import compare

    db_a = _make_receipts_db(
        tmp_path,
        "a.db",
        [
            {
                "receipt_id": "ra1",
                "event_input_hash": "s1",
                "applied_rule_id": None,
                "next_state": "X",
                "outcome": "ok",
                "payload_json": '{"decision_traces": [{"kind": "query", "model_id": "first"}]}',
            }
        ],
    )
    db_b = _make_receipts_db(
        tmp_path,
        "b.db",
        [
            {
                "receipt_id": "rb1",
                "event_input_hash": "s1",
                "applied_rule_id": None,
                "next_state": "X",
                "outcome": "ok",
                "payload_json": '{"decision_traces": [{"kind": "query", "model_id": "second"}]}',
            }
        ],
    )

    report = compare([db_a, db_b])
    assert report.models == ["first", "second"]


def test_compare_skips_db_with_no_model_id(tmp_path: Path) -> None:
    """A DB whose receipts have no model_id is dropped with a warning."""
    from samantha_server.scenarios.compare import compare

    db = _make_receipts_db(
        tmp_path,
        "a.db",
        [
            {
                "receipt_id": "r1",
                "event_input_hash": "scenario1",
                "applied_rule_id": None,
                "next_state": "X",
                "outcome": "ok",
                "payload_json": '{"outcome": "ok"}',  # no decision_traces
            }
        ],
    )

    report = compare([db])
    assert report.models == []
    assert report.events == []


def test_compare_handles_mixed_model_ids_in_single_db(tmp_path: Path) -> None:
    """C3 + M14: a DB containing receipts from two distinct model_ids attributes per-row.

    This is the operator-error failure mode — same RECEIPTS_DB_PATH used
    for two sweep parametrizations. The comparator should attribute each
    receipt to its own model_id (not silently merge under the first
    one) and emit a stderr warning so the operator can investigate.
    """
    from samantha_server.scenarios.compare import compare

    db = _make_receipts_db(
        tmp_path,
        "mixed.db",
        [
            {
                "receipt_id": "r1",
                "event_input_hash": "scenario1",
                "applied_rule_id": None,
                "next_state": "ACCEPTED",
                "outcome": "ok-A",
                "payload_json": '{"decision_traces": [{"kind": "query", "model_id": "model-A"}]}',
                "signed_at_utc": "2026-05-09T00:00:00+00:00",
            },
            {
                "receipt_id": "r2",
                "event_input_hash": "scenario1",
                "applied_rule_id": None,
                "next_state": "ACCEPTED",
                "outcome": "ok-B",
                "payload_json": '{"decision_traces": [{"kind": "query", "model_id": "model-B"}]}',
                "signed_at_utc": "2026-05-09T00:00:01+00:00",
            },
        ],
    )

    report = compare([db])

    assert sorted(report.models) == ["model-A", "model-B"]
    s1 = report.row("scenario1")
    assert dict(s1.verdicts) == {"model-A": "ok-A", "model-B": "ok-B"}
    assert s1.agreement is False


def test_cli_warns_on_mixed_model_db(tmp_path: Path) -> None:
    """The CLI emits a stderr warning when a single DB has multiple model_ids."""
    db = _make_receipts_db(
        tmp_path,
        "mixed.db",
        [
            {
                "receipt_id": "r1",
                "event_input_hash": "s1",
                "applied_rule_id": None,
                "next_state": "X",
                "outcome": "ok",
                "payload_json": '{"decision_traces": [{"kind": "query", "model_id": "M1"}]}',
            },
            {
                "receipt_id": "r2",
                "event_input_hash": "s2",
                "applied_rule_id": None,
                "next_state": "X",
                "outcome": "ok",
                "payload_json": '{"decision_traces": [{"kind": "query", "model_id": "M2"}]}',
            },
        ],
    )

    completed = subprocess.run(
        [sys.executable, "-m", "samantha_server.scenarios.compare", str(db)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "distinct model_ids" in completed.stderr
    assert "M1" in completed.stderr
    assert "M2" in completed.stderr


def test_cli_invocation_prints_table(tmp_path: Path) -> None:
    """``python -m samantha_server.scenarios.compare a.db b.db`` prints a table to stdout."""
    db_a = _make_receipts_db(
        tmp_path,
        "a.db",
        [
            {
                "receipt_id": "ra1",
                "event_input_hash": "scenario1",
                "applied_rule_id": None,
                "next_state": "ACCEPTED",
                "outcome": "ok",
                "payload_json": '{"decision_traces": [{"kind": "query", "model_id": "model-A"}]}',
            }
        ],
    )
    db_b = _make_receipts_db(
        tmp_path,
        "b.db",
        [
            {
                "receipt_id": "rb1",
                "event_input_hash": "scenario1",
                "applied_rule_id": None,
                "next_state": "ACCEPTED",
                "outcome": "ok",
                "payload_json": '{"decision_traces": [{"kind": "query", "model_id": "model-B"}]}',
            }
        ],
    )

    completed = subprocess.run(
        [sys.executable, "-m", "samantha_server.scenarios.compare", str(db_a), str(db_b)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, f"stderr={completed.stderr!r}"
    # Header includes both model names; row mentions the agreed outcome.
    assert "model-A" in completed.stdout
    assert "model-B" in completed.stdout
    assert "scenario1" in completed.stdout


def test_cli_exits_nonzero_on_missing_db(tmp_path: Path) -> None:
    """Non-existent receipt DB → exit code != 0."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "samantha_server.scenarios.compare",
            str(tmp_path / "does-not-exist.db"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    # The error message is bounded — no stack trace echoed verbatim.
    assert "does-not-exist.db" in completed.stderr or "does-not-exist.db" in completed.stdout


def test_cli_exits_nonzero_when_no_comparable_receipts(tmp_path: Path) -> None:
    """H7: empty report → exit code 3 (distinct from missing-DB exit 2 and success 0)."""
    db = _make_receipts_db(
        tmp_path,
        "empty.db",
        [
            {
                "receipt_id": "r1",
                "event_input_hash": "h",
                "applied_rule_id": None,
                "next_state": "X",
                "outcome": "ok",
                # No decision_traces → no model_id → no comparable rows.
                "payload_json": '{"outcome": "ok"}',
            }
        ],
    )

    completed = subprocess.run(
        [sys.executable, "-m", "samantha_server.scenarios.compare", str(db)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 3, f"stdout={completed.stdout!r} stderr={completed.stderr!r}"


def test_cli_requires_at_least_one_db_arg() -> None:
    """No args → usage error (exit code != 0)."""
    completed = subprocess.run(
        [sys.executable, "-m", "samantha_server.scenarios.compare"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0


def test_compare_extracts_model_id_from_first_trace_with_model_id(tmp_path: Path) -> None:
    """When decision_traces has multiple traces, takes model_id from the first that exposes it."""
    from samantha_server.scenarios.compare import compare

    payload = (
        '{"decision_traces": ['
        '{"kind": "primitive", "name": "x"}, '
        '{"kind": "query", "model_id": "real-model"}'
        "]}"
    )
    db = _make_receipts_db(
        tmp_path,
        "a.db",
        [
            {
                "receipt_id": "r1",
                "event_input_hash": "s1",
                "applied_rule_id": None,
                "next_state": "X",
                "outcome": "ok",
                "payload_json": payload,
            }
        ],
    )

    report = compare([db])
    assert report.models == ["real-model"]


# ---------------------------------------------------------------------------
# Direct unit tests of _extract_model_id (M15)
# ---------------------------------------------------------------------------


def test_extract_model_id_raises_on_invalid_json() -> None:
    """M12: corrupt JSON is a hard error, not a silent skip."""
    import json as _json

    from samantha_server.scenarios.compare import _extract_model_id

    with pytest.raises(_json.JSONDecodeError):
        _extract_model_id("not valid json")


def test_extract_model_id_returns_none_when_decision_traces_missing() -> None:
    """Well-formed JSON with no decision_traces → None (refusal pattern)."""
    from samantha_server.scenarios.compare import _extract_model_id

    assert _extract_model_id('{"outcome": "refused"}') is None


def test_extract_model_id_returns_none_when_payload_is_not_dict() -> None:
    """Well-formed but non-dict JSON returns None rather than raising."""
    from samantha_server.scenarios.compare import _extract_model_id

    assert _extract_model_id('"just a string"') is None
    assert _extract_model_id("[1, 2, 3]") is None
    assert _extract_model_id("null") is None


# ---------------------------------------------------------------------------
# Direct unit tests of render() (M13)
# ---------------------------------------------------------------------------


def _make_report(
    models: list[str],
    events: dict[str, tuple[dict[str, str], bool]],
) -> Any:
    """Build a Report directly without round-tripping through DBs."""
    from samantha_server.scenarios.compare import EventRow, Report

    report = Report(models=list(models))
    for event_input_hash, (verdicts, agreement) in events.items():
        report.rows_by_event[event_input_hash] = EventRow(
            event_input_hash=event_input_hash,
            verdicts=MappingProxyType(dict(verdicts)),
            agreement=agreement,
        )
    return report


def test_render_no_models_returns_empty_message() -> None:
    """No comparable receipts → bounded message, no traceback."""
    from samantha_server.scenarios.compare import Report, render

    rendered = render(Report())
    assert "No comparable receipts" in rendered


def test_render_disagreement_arm_emits_NO() -> None:
    """An event where models give different outcomes shows 'NO' in the agree column."""
    from samantha_server.scenarios.compare import render

    report = _make_report(
        models=["model-A", "model-B"],
        events={"s1": ({"model-A": "accepted", "model-B": "rejected"}, False)},
    )
    rendered = render(report)
    assert "NO" in rendered
    assert "1 disagreement(s)" in rendered


def test_render_missing_verdict_shows_em_dash() -> None:
    """A model with no verdict for an event renders as the '—' sentinel."""
    from samantha_server.scenarios.compare import render

    report = _make_report(
        models=["model-A", "model-B"],
        events={"s1": ({"model-A": "accepted"}, True)},
    )
    rendered = render(report)
    assert "—" in rendered


def test_render_summary_line_present() -> None:
    """The summary line counts events, models, and disagreements."""
    from samantha_server.scenarios.compare import render

    report = _make_report(
        models=["A", "B"],
        events={
            "s1": ({"A": "ok", "B": "ok"}, True),
            "s2": ({"A": "ok", "B": "no"}, False),
        },
    )
    rendered = render(report)
    assert "Summary: 2 events across 2 models; 1 disagreement(s)." in rendered


def test_render_model_column_floor_is_16() -> None:
    """A model name shorter than 16 chars still gets a 16-wide column."""
    from samantha_server.scenarios.compare import render

    report = _make_report(
        models=["A", "B"],  # both 1 char
        events={"s1": ({"A": "ok", "B": "ok"}, True)},
    )
    rendered = render(report)
    # The header row must contain "A" padded to ≥16 chars before the column separator.
    header_line = rendered.split("\n")[0]
    # Cells separated by " | "; find "A" padded with spaces.
    cells = header_line.split(" | ")
    a_cell = next((c for c in cells if c.strip() == "A"), None)
    assert a_cell is not None
    assert len(a_cell) == 16


# ---------------------------------------------------------------------------
# Tests for the new C3 mixed-model fix (renamed for clarity)
# ---------------------------------------------------------------------------


def test_compare_warns_on_mixed_model_db_via_capsys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """compare() emits a stderr warning when a DB has multiple model_ids."""
    from samantha_server.scenarios.compare import compare

    db = _make_receipts_db(
        tmp_path,
        "mixed.db",
        [
            {
                "receipt_id": "r1",
                "event_input_hash": "s1",
                "applied_rule_id": None,
                "next_state": "X",
                "outcome": "ok",
                "payload_json": '{"decision_traces": [{"kind": "query", "model_id": "M1"}]}',
            },
            {
                "receipt_id": "r2",
                "event_input_hash": "s2",
                "applied_rule_id": None,
                "next_state": "X",
                "outcome": "ok",
                "payload_json": '{"decision_traces": [{"kind": "query", "model_id": "M2"}]}',
            },
        ],
    )
    compare([db])
    captured = capsys.readouterr()
    assert "distinct model_ids" in captured.err


# L18: removed `_no_receipts_isolation_for_compare` — pytest's autouse
# fixture scoping rules don't allow a sibling fixture in this file to
# suppress the autouse defined in tests/scenarios/conftest.py. The
# previous fixture was a no-op masquerading as suppression. Compare
# tests don't write to the global DB, so the autouse isolation is
# harmless.
