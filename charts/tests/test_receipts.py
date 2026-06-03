"""Slice 5: receipts.py SQLite query helper."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id        TEXT PRIMARY KEY NOT NULL,
    event_input_hash  TEXT NOT NULL,
    applied_rule_id   TEXT,
    next_state        TEXT NOT NULL,
    outcome           TEXT NOT NULL,
    signer_key_id     TEXT NOT NULL,
    signature         BLOB NOT NULL,
    payload_json      TEXT NOT NULL,
    signed_at_utc     TEXT NOT NULL
) STRICT;
"""

_ROW_1 = (
    "rcpt-001",
    "hash-abc",
    "SC-RULE-001",
    "STEP_2",
    "advance",
    "v1",
    b"\x00" * 64,
    "{}",
    "2026-05-01T00:00:00+00:00",
)

_ROW_2 = (
    "rcpt-002",
    "hash-def",
    None,
    "STEP_3",
    "query",
    "v1",
    b"\x01" * 64,
    "{}",
    "2026-05-02T00:00:00+00:00",
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "receipts.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA_SQL)
    conn.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?,?)", _ROW_1)
    conn.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?,?)", _ROW_2)
    conn.commit()
    conn.close()
    return path


def test_query_receipts_shape(db_path: Path) -> None:
    from samantha_charts.data.receipts import query_receipts

    df = query_receipts(db_path)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert list(df.columns) == [
        "receipt_id",
        "scenario_id",
        "outcome",
        "applied_rule_id",
        "signature",
        "signed_at",
    ]


def test_query_receipts_values(db_path: Path) -> None:
    from samantha_charts.data.receipts import query_receipts

    df = query_receipts(db_path)

    row1 = df[df["receipt_id"] == "rcpt-001"].iloc[0]
    assert row1["outcome"] == "advance"
    assert row1["applied_rule_id"] == "SC-RULE-001"
    assert row1["signed_at"] == "2026-05-01T00:00:00+00:00"

    row2 = df[df["receipt_id"] == "rcpt-002"].iloc[0]
    # SQLite NULL → Python None via sqlite3.Row → DataFrame None/NaN.
    # `pd.isna` covers both representations; explicit `is None` would fail on NaN.
    assert pd.isna(row2["applied_rule_id"])


def test_query_receipts_raises_on_missing_db_path(tmp_path: Path) -> None:
    """Missing SQLite file must raise ReceiptsStoreError, not silently create a new DB."""
    from samantha_charts.data.receipts import ReceiptsStoreError, query_receipts

    missing = tmp_path / "does-not-exist.db"

    with pytest.raises(ReceiptsStoreError, match="not found"):
        query_receipts(missing)

    # Guard against the original sqlite3.connect silent-create behavior:
    # the file must remain absent after the failed call.
    assert not missing.exists()


def test_query_receipts_empty_table_returns_empty_dataframe(tmp_path: Path) -> None:
    """A schema-correct but row-empty receipts table returns a typed-empty DataFrame."""
    from samantha_charts.data.receipts import query_receipts

    path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    conn.close()

    df = query_receipts(path)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0
    assert list(df.columns) == [
        "receipt_id",
        "scenario_id",
        "outcome",
        "applied_rule_id",
        "signature",
        "signed_at",
    ]
