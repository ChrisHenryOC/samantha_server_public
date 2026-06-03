"""Tests for new audit query functions: fetch_by_id, fetch_paginated, count_total.

Phase 3 Step 5.5 — GH-120.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_signed_receipt(
    event_hash: str = "c" * 64,
    rule_id: str | None = "ACC-001",
    signed_at_us_offset: int = 0,
) -> object:
    """Return a freshly signed SignedReceipt.

    signed_at_us_offset is unused in construction but documents intent when
    callers need a consistent ordering; actual ordering relies on insertion order
    or explicit timestamp manipulation via _inject_signed_at.
    """
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import sign_decision

    decision = EngineDecision(
        applied_rule_id=rule_id,
        next_state="HOLD" if rule_id else "ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="hold_missing_name" if rule_id else "dispatch_empty",
        also_matched=(),
        dispatched_rule_ids=(rule_id,) if rule_id else (),
        event_input_hash=event_hash,
        primitive_traces={},
        latency_us=10,
    )
    key = bytes(nacl.signing.SigningKey.generate())
    return sign_decision(decision, key_id="v1", signing_key=key)


@pytest.fixture
def write_conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Return an open write connection to a fresh receipts DB."""
    from samantha_server.receipts.store import _open_write_conn, init_store

    db_path = tmp_path / "receipts.db"
    init_store(db_path)
    conn = _open_write_conn(db_path)
    yield conn
    conn.close()


@pytest.fixture
def populated_write_conn(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Return (conn, [receipts]) with 3 receipts inserted."""
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "receipts.db"
    init_store(db_path)

    receipts = [
        _make_signed_receipt(event_hash="a" * 64, rule_id="ACC-001"),
        _make_signed_receipt(event_hash="b" * 64, rule_id="ACC-002"),
        _make_signed_receipt(event_hash="c" * 64, rule_id=None),
    ]

    conn = _open_write_conn(db_path)
    for r in receipts:
        insert(conn, r)  # type: ignore[arg-type]

    yield conn, receipts
    conn.close()


# ---------------------------------------------------------------------------
# Slice 1 — fetch_by_id
# ---------------------------------------------------------------------------


def test_fetch_by_id_returns_matching_receipt(
    populated_write_conn: tuple,
) -> None:
    """fetch_by_id returns the receipt when it exists."""
    from samantha_server.receipts.audit import fetch_by_id
    from samantha_server.receipts.signing import SignedReceipt

    conn, receipts = populated_write_conn
    target = receipts[0]
    assert isinstance(target, SignedReceipt)

    result = fetch_by_id(conn, target.receipt_id)

    assert result is not None
    assert isinstance(result, SignedReceipt)
    assert result.receipt_id == target.receipt_id
    assert result.decision.event_input_hash == target.decision.event_input_hash


def test_fetch_by_id_returns_none_for_unknown_id(
    populated_write_conn: tuple,
) -> None:
    """fetch_by_id returns None when the receipt_id is not present."""
    from samantha_server.receipts.audit import fetch_by_id

    conn, _ = populated_write_conn
    result = fetch_by_id(conn, "01HXXXXXXXXXXXXXXXXXXX00000")

    assert result is None


def test_fetch_by_id_is_exported() -> None:
    """fetch_by_id is in audit.__all__."""
    from samantha_server.receipts import audit

    assert "fetch_by_id" in audit.__all__


# ---------------------------------------------------------------------------
# Slice 2 — fetch_paginated
# ---------------------------------------------------------------------------


def test_fetch_paginated_returns_limit_items(tmp_path: Path) -> None:
    """fetch_paginated with limit=N and offset=0 returns exactly N items."""
    from samantha_server.receipts.audit import fetch_paginated
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "paginated.db"
    init_store(db_path)
    conn = _open_write_conn(db_path)

    # Insert 4 receipts
    receipts = [_make_signed_receipt(event_hash=h * 64) for h in "abcd"]
    for r in receipts:
        insert(conn, r)  # type: ignore[arg-type]

    results = fetch_paginated(conn, offset=0, limit=3)
    assert len(results) == 3
    conn.close()


def test_fetch_paginated_with_offset_returns_remaining(tmp_path: Path) -> None:
    """fetch_paginated at offset=N returns the remaining receipts after the first page."""
    from samantha_server.receipts.audit import fetch_paginated
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "paginated2.db"
    init_store(db_path)
    conn = _open_write_conn(db_path)

    # Insert 4 receipts
    receipts = [_make_signed_receipt(event_hash=h * 64) for h in "abcd"]
    for r in receipts:
        insert(conn, r)  # type: ignore[arg-type]

    page2 = fetch_paginated(conn, offset=3, limit=3)
    assert len(page2) == 1
    conn.close()


def test_fetch_paginated_order_is_descending_by_signed_at(tmp_path: Path) -> None:
    """fetch_paginated returns receipts in descending signed_at_utc order."""
    import sqlite3

    from samantha_server.receipts.audit import fetch_paginated
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "paginated_order.db"
    init_store(db_path)
    conn = _open_write_conn(db_path)

    # Insert 3 receipts with artificially distinct timestamps (update after insert)
    receipts = [_make_signed_receipt(event_hash=h * 64) for h in "abc"]
    for r in receipts:
        insert(conn, r)  # type: ignore[arg-type]

    # Assign explicit signed_at_utc values to force a known ordering.
    # 'a' is oldest, 'b' is middle, 'c' is newest.
    from samantha_server.receipts.signing import SignedReceipt

    assert isinstance(receipts[0], SignedReceipt)
    assert isinstance(receipts[1], SignedReceipt)
    assert isinstance(receipts[2], SignedReceipt)

    raw = sqlite3.connect(str(db_path))
    raw.execute(
        "UPDATE receipts SET signed_at_utc = '2025-01-01T00:00:01+00:00' WHERE receipt_id = ?",
        (receipts[0].receipt_id,),
    )
    raw.execute(
        "UPDATE receipts SET signed_at_utc = '2025-01-01T00:00:02+00:00' WHERE receipt_id = ?",
        (receipts[1].receipt_id,),
    )
    raw.execute(
        "UPDATE receipts SET signed_at_utc = '2025-01-01T00:00:03+00:00' WHERE receipt_id = ?",
        (receipts[2].receipt_id,),
    )
    raw.commit()
    raw.close()

    results = fetch_paginated(conn, offset=0, limit=10)
    assert len(results) == 3
    # First result should be 'c' (newest), last should be 'a' (oldest)
    assert results[0].decision.event_input_hash == "c" * 64
    assert results[2].decision.event_input_hash == "a" * 64
    conn.close()


def test_fetch_paginated_empty_store_returns_empty(tmp_path: Path) -> None:
    """fetch_paginated on empty store returns []."""
    from samantha_server.receipts.audit import fetch_paginated
    from samantha_server.receipts.store import _open_write_conn, init_store

    db_path = tmp_path / "empty.db"
    init_store(db_path)
    conn = _open_write_conn(db_path)

    results = fetch_paginated(conn, offset=0, limit=50)
    assert results == []
    conn.close()


def test_fetch_paginated_past_end_returns_empty(tmp_path: Path) -> None:
    """fetch_paginated with offset past end returns []."""
    from samantha_server.receipts.audit import fetch_paginated
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "past_end.db"
    init_store(db_path)
    conn = _open_write_conn(db_path)

    insert(conn, _make_signed_receipt(event_hash="a" * 64))  # type: ignore[arg-type]

    results = fetch_paginated(conn, offset=10000, limit=50)
    assert results == []
    conn.close()


def test_fetch_paginated_is_exported() -> None:
    """fetch_paginated is in audit.__all__."""
    from samantha_server.receipts import audit

    assert "fetch_paginated" in audit.__all__


# ---------------------------------------------------------------------------
# Slice 3 — count_total
# ---------------------------------------------------------------------------


def test_count_total_empty_store_returns_zero(tmp_path: Path) -> None:
    """count_total on empty store returns 0."""
    from samantha_server.receipts.audit import count_total
    from samantha_server.receipts.store import _open_write_conn, init_store

    db_path = tmp_path / "empty_count.db"
    init_store(db_path)
    conn = _open_write_conn(db_path)

    assert count_total(conn) == 0
    conn.close()


def test_count_total_returns_correct_count(tmp_path: Path) -> None:
    """count_total returns the number of receipts inserted."""
    from samantha_server.receipts.audit import count_total
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "count.db"
    init_store(db_path)
    conn = _open_write_conn(db_path)

    for h in "abcde":
        insert(conn, _make_signed_receipt(event_hash=h * 64))  # type: ignore[arg-type]

    assert count_total(conn) == 5
    conn.close()


def test_count_total_is_exported() -> None:
    """count_total is in audit.__all__."""
    from samantha_server.receipts import audit

    assert "count_total" in audit.__all__


# ---------------------------------------------------------------------------
# Slice 5 — receipt_id DESC tie-break (#5)
# ---------------------------------------------------------------------------


def test_fetch_paginated_receipt_id_desc_tiebreak(tmp_path: Path) -> None:
    """fetch_paginated uses receipt_id DESC as the tie-break when signed_at_utc is equal.

    When two receipts share the same signed_at_utc, the lexicographically
    larger receipt_id (ULID — newer ULIDs are lexicographically larger) must
    come first.
    """
    import sqlite3

    from samantha_server.receipts.audit import fetch_paginated
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "tiebreak.db"
    init_store(db_path)
    conn = _open_write_conn(db_path)

    r1 = _make_signed_receipt(event_hash="d" * 64)
    r2 = _make_signed_receipt(event_hash="e" * 64)
    insert(conn, r1)  # type: ignore[arg-type]
    insert(conn, r2)  # type: ignore[arg-type]

    assert isinstance(r1, SignedReceipt)
    assert isinstance(r2, SignedReceipt)

    # Force both to the same signed_at_utc to exercise the tie-break.
    same_ts = "2025-06-01T00:00:00+00:00"
    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE receipts SET signed_at_utc = ?", (same_ts,))
    raw.commit()
    raw.close()

    results = fetch_paginated(conn, offset=0, limit=10)
    assert len(results) == 2

    # Lex-larger receipt_id must come first (receipt_id DESC tie-break).
    first_id, second_id = results[0].receipt_id, results[1].receipt_id
    assert first_id > second_id, f"Expected receipt_id DESC tie-break: {first_id!r} > {second_id!r}"

    conn.close()


# ---------------------------------------------------------------------------
# Slice 4 — fetch_paginated corrupt-row skip-and-log (#3)
# ---------------------------------------------------------------------------


def test_fetch_paginated_skips_corrupt_row_and_logs_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """fetch_paginated skips a corrupt middle row and logs a WARNING.

    Regression for GH-120 review finding #3: the list comprehension in the
    original implementation would raise on a single bad row and abort the
    entire page. The fixed version matches the skip-and-log pattern in
    fetch_by_outcome / fetch_by_query_text_hash.
    """
    import logging
    import sqlite3

    from samantha_server.receipts.audit import fetch_paginated
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "corrupt_row.db"
    init_store(db_path)
    conn = _open_write_conn(db_path)

    # Insert 3 receipts
    receipts = [_make_signed_receipt(event_hash=h * 64) for h in "xyz"]
    for r in receipts:
        insert(conn, r)  # type: ignore[arg-type]

    from samantha_server.receipts.signing import SignedReceipt

    assert isinstance(receipts[1], SignedReceipt)
    middle_id = receipts[1].receipt_id

    # Corrupt the middle receipt's payload_json directly via a raw connection
    raw = sqlite3.connect(str(db_path))
    raw.execute(
        "UPDATE receipts SET payload_json = 'NOT_VALID_JSON' WHERE receipt_id = ?",
        (middle_id,),
    )
    raw.commit()
    raw.close()

    with caplog.at_level(logging.WARNING, logger="samantha_server.receipts.audit"):
        results = fetch_paginated(conn, offset=0, limit=10)

    # 2 valid rows returned, corrupt one skipped
    assert len(results) == 2
    result_ids = {r.receipt_id for r in results}
    assert middle_id not in result_ids

    # A WARNING was emitted identifying the bad row
    assert any(
        middle_id in record.message
        for record in caplog.records
        if record.levelno == logging.WARNING
    )

    conn.close()
