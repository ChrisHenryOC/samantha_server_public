"""Multi-model comparison reporter for the replay corpus (GH-141).

Reads multiple per-model receipt DBs (each populated by a single
parametrization of the ``model_under_test`` sweep fixture in
``samantha_server.scenarios.sweep``) and emits a per-scenario
agreement / disagreement matrix grouped by ``model_id``.

CLI usage:

    python -m samantha_server.scenarios.compare receipts1.db receipts2.db ...

Programmatic usage:

    from samantha_server.scenarios.compare import compare
    report = compare([Path("a.db"), Path("b.db")])

The "scenario" identity is the receipt's ``event_input_hash`` — it is
deterministic over the input ``SpecimenContext``, so two models running
the same input produce two receipts with the same hash.

Attribution: each receipt is attributed to its own ``model_id`` (read
from the canonical-JSON payload's ``decision_traces``). A single DB
that mixes receipts from multiple model_ids — e.g., an operator
reusing ``RECEIPTS_DB_PATH`` for two sweep runs — is reported on
correctly: each row counts toward its own model. The CLI emits a
stderr warning when a DB contains more than one distinct model_id so
the operator can investigate the path-reuse failure mode.

This module is read-only over receipt DBs; it does not import or
construct an LLM client and does not call into the engine. The
comparator does NOT verify receipt signatures — that is the audit
path's job (`samantha_server.receipts.audit`); the comparator is
downstream of integrity verification.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True)
class ReceiptRow:
    """One row pulled from a receipt DB, narrowed to the comparator's needs.

    ``model_id`` may be ``None`` for receipts whose payloads don't
    expose one (e.g., refusals before any LLM call). Such rows are
    visible to the loader but the report skips them — comparing
    "model-less" rows isn't meaningful.
    """

    receipt_id: str
    event_input_hash: str
    next_state: str
    outcome: str
    model_id: str | None


@dataclass(frozen=True)
class EventRow:
    """One row in the comparison report — verdicts grouped by model for a single event.

    ``verdicts`` is a read-only ``Mapping`` (an immutable
    ``MappingProxyType``) so the frozen-dataclass guarantee actually
    extends to the values, not just the field bindings.
    """

    event_input_hash: str
    verdicts: Mapping[str, str]
    agreement: bool


@dataclass
class Report:
    """Comparison report across N model receipt DBs."""

    models: list[str] = field(default_factory=list)
    rows_by_event: dict[str, EventRow] = field(default_factory=dict)

    @property
    def events(self) -> list[str]:
        return sorted(self.rows_by_event.keys())

    def row(self, event_input_hash: str) -> EventRow:
        return self.rows_by_event[event_input_hash]


# ---------------------------------------------------------------------------
# DB I/O
# ---------------------------------------------------------------------------


def _extract_model_id(payload_json: str) -> str | None:
    """Return ``model_id`` from the first decision_trace that has one, else ``None``.

    Distinguishes corrupt JSON (raise) from missing field (return None).
    The receipt store produces well-formed JSON by construction; if a
    receipt's ``payload_json`` won't parse, that's a corrupt store and a
    hard error — silently mapping it to ``None`` would mask data
    corruption.
    """
    payload = json.loads(payload_json)
    if not isinstance(payload, dict):
        return None
    traces = payload.get("decision_traces")
    if not isinstance(traces, list):
        return None
    for trace in traces:
        if isinstance(trace, dict):
            model_id = trace.get("model_id")
            if isinstance(model_id, str) and model_id:
                return model_id
    return None


def load_receipts(db_path: Path) -> list[ReceiptRow]:
    """Return every receipt in ``db_path`` as a list of :class:`ReceiptRow`.

    Returns a list rather than an iterator so the connection close is
    bounded by this function's body — a partial-consumption caller of
    a generator would have leaked the connection. Callers materialize
    everything anyway today; this just makes the contract honest.

    Raises ``FileNotFoundError`` if the path does not exist.

    Uses ``PRAGMA query_only=1`` for read-only access (mirrors
    ``store._open_audit_conn``) instead of constructing a SQLite URI
    — URI construction without percent-encoding is a footgun (a
    ``&`` in the path injects extra URI parameters).
    """
    if not db_path.exists():
        raise FileNotFoundError(f"receipt DB not found: {db_path}")

    conn = sqlite3.connect(str(db_path.resolve()))
    try:
        conn.execute("PRAGMA query_only = 1")
        cur = conn.cursor()
        cur.execute(
            """
            SELECT receipt_id, event_input_hash, next_state, outcome, payload_json
            FROM receipts
            ORDER BY signed_at_utc, receipt_id
            """
        )
        rows: list[ReceiptRow] = []
        for receipt_id, event_input_hash, next_state, outcome, payload_json in cur:
            rows.append(
                ReceiptRow(
                    receipt_id=receipt_id,
                    event_input_hash=event_input_hash,
                    next_state=next_state,
                    outcome=outcome,
                    model_id=_extract_model_id(payload_json),
                )
            )
        return rows
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def compare(db_paths: Iterable[Path]) -> Report:
    """Build a :class:`Report` from a sequence of receipt DBs.

    Per-row attribution: each receipt's ``model_id`` is read from its
    own payload, not assumed to be DB-wide. A single DB with multiple
    distinct model_ids (operator reused ``RECEIPTS_DB_PATH``) is
    reported on row-by-row and a stderr warning surfaces the path-reuse
    failure mode.

    DBs are processed in input order; ``Report.models`` preserves the
    order in which model_ids are first seen so the CLI's table columns
    are stable across runs.

    Rows whose payload doesn't expose a ``model_id`` (refusals, etc.)
    are dropped — comparing model-less rows isn't meaningful.
    """
    report = Report()
    # event_input_hash → {model_id: outcome}.
    verdicts: dict[str, dict[str, str]] = {}

    for db_path in db_paths:
        rows = load_receipts(db_path)
        models_in_db: set[str] = set()
        comparable = 0
        for row in rows:
            if row.model_id is None:
                continue
            comparable += 1
            models_in_db.add(row.model_id)
            if row.model_id not in report.models:
                report.models.append(row.model_id)
            event = verdicts.setdefault(row.event_input_hash, {})
            event[row.model_id] = row.outcome

        if comparable == 0:
            print(
                f"warning: {db_path}: no receipt exposes a model_id; skipping",
                file=sys.stderr,
            )
            continue

        if len(models_in_db) > 1:
            print(
                f"warning: {db_path}: contains receipts from "
                f"{len(models_in_db)} distinct model_ids ({sorted(models_in_db)}); "
                "this typically means RECEIPTS_DB_PATH was reused across "
                "sweep runs. Per-row attribution is applied.",
                file=sys.stderr,
            )

    for event_input_hash, model_to_outcome in verdicts.items():
        unique_outcomes = set(model_to_outcome.values())
        report.rows_by_event[event_input_hash] = EventRow(
            event_input_hash=event_input_hash,
            verdicts=MappingProxyType(dict(model_to_outcome)),
            agreement=len(unique_outcomes) == 1,
        )

    return report


# ---------------------------------------------------------------------------
# CLI rendering
# ---------------------------------------------------------------------------


def render(report: Report) -> str:
    """Render the report as a fixed-width text table."""
    if not report.models:
        return "No comparable receipts (no model_id surfaced in any DB).\n"

    events = report.events  # cache: report.events sorts on every access

    event_col_width = max(8, max((len(e) for e in events), default=8))
    model_col_widths = {m: max(len(m), 16) for m in report.models}
    agree_col_width = len("agree?")

    header_cells = [
        "event_input_hash".ljust(event_col_width),
        *[m.ljust(model_col_widths[m]) for m in report.models],
        "agree?".ljust(agree_col_width),
    ]
    separator_cells = [
        "-" * event_col_width,
        *["-" * model_col_widths[m] for m in report.models],
        "-" * agree_col_width,
    ]
    lines = [" | ".join(header_cells), "-+-".join(separator_cells)]

    disagreements = 0
    for event in events:
        row = report.row(event)
        if not row.agreement:
            disagreements += 1
        cells = [
            event.ljust(event_col_width),
            *[(row.verdicts.get(m, "—")).ljust(model_col_widths[m]) for m in report.models],
            ("yes" if row.agreement else "NO").ljust(agree_col_width),
        ]
        lines.append(" | ".join(cells))

    lines.append("")
    lines.append(
        f"Summary: {len(events)} events across {len(report.models)} models; "
        f"{disagreements} disagreement(s)."
    )
    return "\n".join(lines) + "\n"


# Exit codes:
#   0 — report rendered with at least one comparable event.
#   2 — argparse / missing-DB error (argparse defaults).
#   3 — every input DB had no comparable receipts. Distinct from 0 so
#       automation (CI gate) can detect "the sweep produced nothing"
#       without parsing stdout.
_EXIT_NO_COMPARABLE_RECEIPTS: int = 3


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="samantha_server.scenarios.compare",
        description=(
            "Compare receipt DBs across multiple model runs and emit "
            "a per-scenario agreement / disagreement matrix."
        ),
    )
    parser.add_argument(
        "receipt_dbs",
        nargs="+",
        type=Path,
        help="One or more receipt DB paths (e.g., produced by the model_under_test sweep).",
    )
    args = parser.parse_args(argv)

    db_paths: list[Path] = args.receipt_dbs
    for path in db_paths:
        if not path.exists():
            print(f"error: receipt DB does not exist: {path}", file=sys.stderr)
            return 2

    report = compare(db_paths)
    sys.stdout.write(render(report))
    if not report.models:
        return _EXIT_NO_COMPARABLE_RECEIPTS
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
