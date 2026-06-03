"""Query signed receipts from the samantha_server SQLite store.

Does NOT import samantha_server — reads the schema directly via SQL.

Column mapping from the receipts table to the returned DataFrame:
  receipt_id       → receipt_id
  event_input_hash → scenario_id  (renamed for charting consistency; the
                                   hash IS the scenario identifier in the
                                   receipts schema — there is no separate
                                   ``scenario_id`` column)
  outcome          → outcome
  applied_rule_id  → applied_rule_id
  signature        → signature
  signed_at_utc    → signed_at
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


class ReceiptsStoreError(FileNotFoundError):
    """Raised when the receipts SQLite path doesn't exist on disk.

    Subclasses :class:`FileNotFoundError` so callers that already handle
    missing-file errors via the standard hierarchy keep working, while
    catching ``ReceiptsStoreError`` specifically remains an option.
    """


_COLUMNS = [
    "receipt_id",
    "scenario_id",
    "outcome",
    "applied_rule_id",
    "signature",
    "signed_at",
]

_SELECT_SQL = """
SELECT
    receipt_id,
    event_input_hash  AS scenario_id,
    outcome,
    applied_rule_id,
    signature,
    signed_at_utc     AS signed_at
FROM receipts
ORDER BY signed_at_utc ASC
"""


def query_receipts(db_path: Path) -> pd.DataFrame:
    """Load all receipts from the SQLite store at *db_path*.

    Parameters
    ----------
    db_path:
        Path to the receipts SQLite file written by samantha_server.

    Returns
    -------
    pd.DataFrame
        Columns: receipt_id, scenario_id, outcome, applied_rule_id,
        signature, signed_at. Empty DataFrame (with those columns) if the
        table has no rows.

    Raises
    ------
    ReceiptsStoreError
        If *db_path* does not exist. Guards against
        :func:`sqlite3.connect`'s silent-create-on-missing-file behavior
        (which would otherwise surface as a confusing
        ``OperationalError: no such table: receipts``).
    """
    if not db_path.exists():
        raise ReceiptsStoreError(
            f"Receipts SQLite file not found: {db_path}. "
            "Did the sweep run with --receipts-db-path pointed at this location? "
            "(The replay CLI defaults to :memory:; explicit path needed for charting.)"
        )

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(_SELECT_SQL)
        rows = cursor.fetchall()
    finally:
        conn.close()

    return pd.DataFrame([dict(row) for row in rows], columns=_COLUMNS)
