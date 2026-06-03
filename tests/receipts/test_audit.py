"""Tests for samantha_server.receipts.audit (Phase 2 Step 6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _make_signed_receipt(event_hash: str = "c" * 64, rule_id: str | None = "ACC-001") -> object:
    """Return a freshly signed SignedReceipt."""
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
def populated_db(tmp_path: Path) -> tuple[Path, list[object]]:
    """Return (db_path, [receipts]) with 3 receipts inserted."""
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "receipts.db"
    init_store(db_path)

    receipts = [
        _make_signed_receipt(event_hash="a" * 64, rule_id="ACC-001"),
        _make_signed_receipt(event_hash="b" * 64, rule_id="ACC-002"),
        _make_signed_receipt(event_hash="a" * 64, rule_id=None),  # dispatch_empty
    ]

    conn = _open_write_conn(db_path)
    for r in receipts:
        insert(conn, r)  # type: ignore[arg-type]
    conn.commit()
    conn.close()

    return db_path, receipts


# ---------------------------------------------------------------------------
# fetch_by_event_hash
# ---------------------------------------------------------------------------


def test_fetch_by_event_hash_returns_matching_receipts(
    populated_db: tuple[Path, list[object]],
) -> None:
    from samantha_server.receipts.audit import fetch_by_event_hash
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_audit_conn

    db_path, receipts = populated_db
    conn = _open_audit_conn(db_path)
    results = fetch_by_event_hash(conn, "a" * 64)
    conn.close()

    assert len(results) == 2
    for r in results:
        assert isinstance(r, SignedReceipt)
        assert r.decision.event_input_hash == "a" * 64


def test_fetch_by_event_hash_returns_empty_for_unknown_hash(
    populated_db: tuple[Path, list[object]],
) -> None:
    from samantha_server.receipts.audit import fetch_by_event_hash
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = populated_db
    conn = _open_audit_conn(db_path)
    results = fetch_by_event_hash(conn, "z" * 64)
    conn.close()

    assert results == []


def test_fetch_by_event_hash_rehydrates_as_signed_receipt(
    populated_db: tuple[Path, list[object]],
) -> None:
    from samantha_server.receipts.audit import fetch_by_event_hash
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = populated_db
    conn = _open_audit_conn(db_path)
    results = fetch_by_event_hash(conn, "b" * 64)
    conn.close()

    assert len(results) == 1
    r = results[0]
    assert isinstance(r, SignedReceipt)
    assert r.decision.applied_rule_id == "ACC-002"


# ---------------------------------------------------------------------------
# fetch_by_rule_id
# ---------------------------------------------------------------------------


def test_fetch_by_rule_id_returns_matching_receipts(
    populated_db: tuple[Path, list[object]],
) -> None:
    from samantha_server.receipts.audit import fetch_by_rule_id
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = populated_db
    conn = _open_audit_conn(db_path)
    results = fetch_by_rule_id(conn, "ACC-001")
    conn.close()

    assert len(results) == 1
    assert isinstance(results[0], SignedReceipt)
    assert results[0].decision.applied_rule_id == "ACC-001"


def test_fetch_by_rule_id_none_returns_dispatch_empty(
    populated_db: tuple[Path, list[object]],
) -> None:
    """fetch_by_rule_id(conn, None) returns receipts with NULL applied_rule_id."""
    from samantha_server.receipts.audit import fetch_by_rule_id
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = populated_db
    conn = _open_audit_conn(db_path)
    results = fetch_by_rule_id(conn, None)
    conn.close()

    assert len(results) == 1
    assert isinstance(results[0], SignedReceipt)
    assert results[0].decision.applied_rule_id is None


def test_fetch_by_rule_id_returns_empty_for_unknown_rule(
    populated_db: tuple[Path, list[object]],
) -> None:
    from samantha_server.receipts.audit import fetch_by_rule_id
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = populated_db
    conn = _open_audit_conn(db_path)
    results = fetch_by_rule_id(conn, "ACC-999")
    conn.close()

    assert results == []


# ---------------------------------------------------------------------------
# fetch_in_window
# ---------------------------------------------------------------------------


def test_fetch_in_window_returns_receipts_in_range(
    populated_db: tuple[Path, list[object]],
) -> None:
    from samantha_server.receipts.audit import fetch_in_window
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = populated_db
    now = datetime.now(tz=UTC)
    start = now - timedelta(minutes=1)
    end = now + timedelta(minutes=1)

    conn = _open_audit_conn(db_path)
    results = fetch_in_window(conn, start, end)
    conn.close()

    assert len(results) == 3
    for r in results:
        assert isinstance(r, SignedReceipt)


def test_fetch_in_window_excludes_out_of_range(
    populated_db: tuple[Path, list[object]],
) -> None:
    from samantha_server.receipts.audit import fetch_in_window
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = populated_db
    # Window in the past — no receipts should match
    past = datetime(2000, 1, 1, tzinfo=UTC)
    end = datetime(2000, 1, 2, tzinfo=UTC)

    conn = _open_audit_conn(db_path)
    results = fetch_in_window(conn, past, end)
    conn.close()

    assert results == []


def test_fetch_in_window_single_instant_boundary(
    populated_db: tuple[Path, list[object]],
) -> None:
    """L-06: start_utc == end_utc == receipt.signed_at_utc is inclusive on both sides."""
    from samantha_server.receipts.audit import fetch_in_window
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_audit_conn

    db_path, receipts = populated_db
    # Take one receipt whose exact signed_at_utc we know
    receipt = receipts[0]
    assert isinstance(receipt, SignedReceipt)
    exact_ts = receipt.signed_at_utc

    conn = _open_audit_conn(db_path)
    results = fetch_in_window(conn, exact_ts, exact_ts)  # zero-width window
    conn.close()

    # At least the receipt at exact_ts should be returned
    result_ids = {r.receipt_id for r in results}
    assert receipt.receipt_id in result_ids, (
        "fetch_in_window with start == end == signed_at_utc must include the receipt"
    )


def test_fetch_in_window_rejects_naive_start() -> None:
    """fetch_in_window raises ValueError for naive (non-UTC) start datetime."""
    import sqlite3

    from samantha_server.receipts.audit import fetch_in_window

    conn = sqlite3.connect(":memory:")
    naive_start = datetime(2025, 1, 1)  # naive

    with pytest.raises(ValueError, match="UTC"):
        fetch_in_window(conn, naive_start, datetime.now(tz=UTC))
    conn.close()


def test_fetch_in_window_rejects_naive_end() -> None:
    """fetch_in_window raises ValueError for naive end datetime."""
    import sqlite3

    from samantha_server.receipts.audit import fetch_in_window

    conn = sqlite3.connect(":memory:")
    naive_end = datetime(2025, 1, 1)  # naive

    with pytest.raises(ValueError, match="UTC"):
        fetch_in_window(conn, datetime.now(tz=UTC), naive_end)
    conn.close()


# ---------------------------------------------------------------------------
# verify_signature re-export
# ---------------------------------------------------------------------------


def test_row_to_receipt_raises_on_naive_signed_at(tmp_path: object) -> None:
    """M-22: a naive signed_at_utc in the DB raises ReceiptPersistenceError on read.

    The naive-tz repair branch (signed_at.replace(tzinfo=UTC)) has been removed.
    A naive timestamp stored in the DB now fails SignedReceipt._enforce_utc and is
    caught by the ValidationError → ReceiptPersistenceError mapping.
    """
    import sqlite3
    from pathlib import Path

    from samantha_server.errors import ReceiptPersistenceError
    from samantha_server.receipts.audit import fetch_by_event_hash
    from samantha_server.receipts.store import (
        _open_audit_conn,
        _open_write_conn,
        init_store,
        insert,
    )

    db_path = Path(str(tmp_path)) / "naive_ts.db"  # type: ignore[arg-type]
    init_store(db_path)

    receipt = _make_signed_receipt(event_hash="n" * 64)
    write_conn = _open_write_conn(db_path)
    insert(write_conn, receipt)  # type: ignore[arg-type]
    write_conn.close()

    # Corrupt signed_at_utc to a naive ISO-8601 string (no +00:00 suffix)
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.execute(
        "UPDATE receipts SET signed_at_utc = '2025-01-01T00:00:00' WHERE event_input_hash = ?",
        ("n" * 64,),
    )
    raw_conn.commit()
    raw_conn.close()

    audit_conn = _open_audit_conn(db_path)
    with pytest.raises(ReceiptPersistenceError):
        fetch_by_event_hash(audit_conn, "n" * 64)
    audit_conn.close()


def test_fetch_by_event_hash_raises_on_malformed_payload(tmp_path: object) -> None:
    """H-06: A row with invalid EngineDecision JSON raises ReceiptPersistenceError.

    Simulates a tampered or schema-changed row with payload_json='{}' (valid JSON
    but not a valid EngineDecision). _row_to_receipt must wrap the ValidationError
    in ReceiptPersistenceError instead of surfacing a raw pydantic.ValidationError.
    """
    import sqlite3
    from pathlib import Path

    from samantha_server.errors import ReceiptPersistenceError
    from samantha_server.receipts.audit import fetch_by_event_hash
    from samantha_server.receipts.store import (
        _open_audit_conn,
        _open_write_conn,
        init_store,
        insert,
    )

    db_path = Path(str(tmp_path)) / "malformed.db"  # type: ignore[arg-type]
    init_store(db_path)

    receipt = _make_signed_receipt(event_hash="z" * 64)
    write_conn = _open_write_conn(db_path)
    insert(write_conn, receipt)  # type: ignore[arg-type]
    write_conn.close()

    # Corrupt payload_json to valid JSON but invalid EngineDecision
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.execute("PRAGMA query_only=0")
    raw_conn.execute(
        "UPDATE receipts SET payload_json = '{}' WHERE event_input_hash = ?",
        ("z" * 64,),
    )
    raw_conn.commit()
    raw_conn.close()

    audit_conn = _open_audit_conn(db_path)
    with pytest.raises(ReceiptPersistenceError, match="malformed"):
        fetch_by_event_hash(audit_conn, "z" * 64)
    audit_conn.close()


def test_audit_module_re_exports_verify_signature() -> None:
    """audit.verify_signature is the same function as signing.verify_signature."""
    from samantha_server.receipts import audit
    from samantha_server.receipts.signing import verify_signature

    assert audit.verify_signature is verify_signature


# ---------------------------------------------------------------------------
# GH-88 Slice 12: fetch_by_outcome
# ---------------------------------------------------------------------------


def _make_signed_receipt_with_outcome(
    event_hash: str,
    outcome: str,
    rule_id: str | None = None,
) -> object:
    """Return a signed receipt with the given outcome."""
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import sign_decision

    decision = EngineDecision(
        applied_rule_id=rule_id,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome=outcome,
        also_matched=(),
        dispatched_rule_ids=(rule_id,) if rule_id else (),
        event_input_hash=event_hash,
        primitive_traces={},
        latency_us=0,
    )
    key = bytes(nacl.signing.SigningKey.generate())
    return sign_decision(decision, key_id="v1", signing_key=key)


@pytest.fixture
def outcome_db(tmp_path: Path) -> tuple[Path, list[object]]:
    """Return (db_path, [receipts]) with mixed outcomes."""
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "outcome.db"
    init_store(db_path)

    receipts = [
        _make_signed_receipt_with_outcome("a" * 64, "refused_ungrounded"),
        _make_signed_receipt_with_outcome("b" * 64, "refused_ungrounded"),
        _make_signed_receipt_with_outcome("c" * 64, "hold_missing_name", rule_id="ACC-001"),
        _make_signed_receipt_with_outcome("d" * 64, "refused_no_skill_for_state"),
    ]

    conn = _open_write_conn(str(db_path))
    for r in receipts:
        insert(conn, r)  # type: ignore[arg-type]
    return db_path, receipts


def test_fetch_by_outcome_returns_matching_receipts(outcome_db: tuple) -> None:
    """fetch_by_outcome returns only receipts with the given outcome."""
    import sqlite3

    from samantha_server.receipts.audit import fetch_by_outcome

    db_path, receipts = outcome_db
    conn = sqlite3.connect(str(db_path))
    results = fetch_by_outcome(conn, "refused_ungrounded")
    assert len(results) == 2
    for r in results:
        assert r.decision.outcome == "refused_ungrounded"


def test_fetch_by_outcome_returns_empty_for_no_match(outcome_db: tuple) -> None:
    """fetch_by_outcome returns empty tuple/list when no receipts match."""
    import sqlite3

    from samantha_server.receipts.audit import fetch_by_outcome

    db_path, _ = outcome_db
    conn = sqlite3.connect(str(db_path))
    results = fetch_by_outcome(conn, "nonexistent_outcome")
    assert len(results) == 0


def test_fetch_by_outcome_single_match(outcome_db: tuple) -> None:
    """fetch_by_outcome with outcome that appears exactly once."""
    import sqlite3

    from samantha_server.receipts.audit import fetch_by_outcome

    db_path, _ = outcome_db
    conn = sqlite3.connect(str(db_path))
    results = fetch_by_outcome(conn, "refused_no_skill_for_state")
    assert len(results) == 1
    assert results[0].decision.outcome == "refused_no_skill_for_state"


def test_fetch_by_outcome_does_not_support_wildcard(outcome_db: tuple) -> None:
    """fetch_by_outcome does NOT support wildcards — exact match only."""
    import sqlite3

    from samantha_server.receipts.audit import fetch_by_outcome

    db_path, _ = outcome_db
    conn = sqlite3.connect(str(db_path))
    # "refused_*" should return nothing (wildcard not supported)
    results = fetch_by_outcome(conn, "refused_*")
    assert len(results) == 0


def test_fetch_by_outcome_is_in_all() -> None:
    """fetch_by_outcome is exported from audit.__all__."""
    from samantha_server.receipts import audit

    assert "fetch_by_outcome" in audit.__all__


# ---------------------------------------------------------------------------
# C-03: fetch_by_outcome skip-and-log on malformed rows
# ---------------------------------------------------------------------------


def test_fetch_by_outcome_skips_malformed_rows_and_returns_valid(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """C-03: a malformed row in the receipts table must be skipped (not abort).

    Mirrors the fetch_by_query_text_hash M-03 pattern. A single corrupted
    payload_json must not prevent valid rows from being returned.
    """
    import logging
    import sqlite3

    from samantha_server.receipts.audit import fetch_by_outcome
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "malformed_outcome.db"
    init_store(db_path)

    # Insert a valid receipt first
    valid_receipt = _make_signed_receipt_with_outcome("v" * 64, "refused_ungrounded")
    conn_write = _open_write_conn(str(db_path))
    insert(conn_write, valid_receipt)  # type: ignore[arg-type]
    conn_write.commit()

    # Insert a malformed row via raw SQL (corrupted payload_json)
    conn_write.execute(
        "INSERT INTO receipts "
        "(receipt_id, event_input_hash, applied_rule_id, next_state, "
        "outcome, signer_key_id, signature, payload_json, signed_at_utc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "malformed-id-1",
            "m" * 64,
            None,
            "ACCESSIONING",
            "refused_ungrounded",
            "v1",
            b"\x00" * 64,
            "NOT VALID JSON {{{",  # corrupted payload_json
            "2024-01-01T00:00:00+00:00",
        ),
    )
    conn_write.commit()

    # Verify: fetch_by_outcome returns the valid receipt, skips the malformed one,
    # and logs a warning.
    conn_read = sqlite3.connect(str(db_path))
    with caplog.at_level(logging.WARNING, logger="samantha_server.receipts.audit"):
        results = fetch_by_outcome(conn_read, "refused_ungrounded")

    assert len(results) == 1, "Valid receipt must be returned despite malformed row"
    assert results[0].decision.event_input_hash == "v" * 64
    assert any("malformed" in record.message.lower() for record in caplog.records), (
        "A warning must be logged for the malformed row"
    )


# ---------------------------------------------------------------------------
# M-12: outcome index exists and is used by fetch_by_outcome
# ---------------------------------------------------------------------------


def test_outcome_index_exists(tmp_path: Path) -> None:
    """M-12: schema.sql must create idx_receipts_outcome on the outcome column."""
    import sqlite3

    from samantha_server.receipts.store import init_store

    db_path = tmp_path / "idx_test.db"
    init_store(db_path)

    conn = sqlite3.connect(str(db_path))
    # sqlite_master contains index info
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_receipts_outcome'"
    )
    row = cursor.fetchone()
    assert row is not None, (
        "idx_receipts_outcome index must exist in the receipts schema "
        "(required for O(log N) fetch_by_outcome queries)"
    )


# ---------------------------------------------------------------------------
# L-04: RefusalTrace round-trip in fetch_by_outcome
# ---------------------------------------------------------------------------


def test_fetch_by_outcome_refusaltrace_round_trip(tmp_path: Path) -> None:
    """L-04: fetch_by_outcome correctly rehydrates receipts with RefusalTrace decision_traces.

    A receipt carrying RefusalTrace decision_traces must be retrievable via
    fetch_by_outcome and have its decision_traces intact.
    """
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision, RefusalTrace
    from samantha_server.receipts.audit import fetch_by_outcome
    from samantha_server.receipts.signing import SignedReceipt, sign_decision
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "refusal_rt.db"
    init_store(db_path)

    # Build a receipt with a RefusalTrace
    trace = RefusalTrace(
        refusal_reason="STAGE_PRE_SKILL_UNAVAILABLE",
        refusal_stage="PRE",
        judge_verdict=None,
        alternatives=(),
    )
    decision = EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="refused_skill_unavailable",
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash="r" * 64,
        primitive_traces={},
        latency_us=0,
        decision_traces=(trace,),
    )
    key = bytes(nacl.signing.SigningKey.generate())
    receipt = sign_decision(decision, key_id="v1", signing_key=key)

    conn = _open_write_conn(str(db_path))
    insert(conn, receipt)  # type: ignore[arg-type]
    conn.commit()

    # Fetch back via outcome
    import sqlite3

    conn_read = sqlite3.connect(str(db_path))
    results = fetch_by_outcome(conn_read, "refused_skill_unavailable")

    assert len(results) == 1
    r = results[0]
    assert isinstance(r, SignedReceipt)
    assert r.decision.outcome == "refused_skill_unavailable"
    assert len(r.decision.decision_traces) == 1
    rehydrated_trace = r.decision.decision_traces[0]
    assert isinstance(rehydrated_trace, RefusalTrace)
    assert rehydrated_trace.refusal_reason == "STAGE_PRE_SKILL_UNAVAILABLE"
    assert rehydrated_trace.refusal_stage == "PRE"


# ---------------------------------------------------------------------------
# GH-367: fetch_by_order_id
# ---------------------------------------------------------------------------


def _make_receipt_with_order_id(order_id: str | None, event_hash: str = "e" * 64) -> object:
    """Return a freshly signed SignedReceipt with the given order_id."""
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import sign_decision

    decision = EngineDecision(
        applied_rule_id="ACC-001",
        next_state="HOLD",
        flags_added=(),
        flags_cleared=(),
        outcome="held_missing_name",
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash=event_hash,
        primitive_traces={},
        latency_us=5,
        order_id=order_id,
    )
    key = bytes(nacl.signing.SigningKey.generate())
    return sign_decision(decision, key_id="v1", signing_key=key)


@pytest.fixture
def order_id_db(tmp_path: Path) -> tuple[Path, list[object]]:
    """DB with 3 receipts: order_id='O-A', order_id='O-B', order_id=None."""
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "order_id_receipts.db"
    init_store(db_path)

    receipts = [
        _make_receipt_with_order_id("O-A", event_hash="a1" * 32),
        _make_receipt_with_order_id("O-B", event_hash="b1" * 32),
        _make_receipt_with_order_id(None, event_hash="c1" * 32),
        _make_receipt_with_order_id("O-A", event_hash="a2" * 32),  # second for O-A
    ]

    conn = _open_write_conn(db_path)
    for r in receipts:
        insert(conn, r)  # type: ignore[arg-type]
    conn.commit()
    conn.close()
    return db_path, receipts


def test_fetch_by_order_id_returns_matching_receipts(
    order_id_db: tuple[Path, list[object]],
) -> None:
    """fetch_by_order_id returns all receipts for a given order_id in insertion order."""
    from samantha_server.receipts.audit import fetch_by_order_id
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_audit_conn

    db_path, receipts = order_id_db
    conn = _open_audit_conn(db_path)
    results = fetch_by_order_id(conn, "O-A")
    conn.close()

    assert len(results) == 2
    for r in results:
        assert isinstance(r, SignedReceipt)
        assert r.decision.order_id == "O-A"

    # Insertion-order guard: the fixture inserts receipts[0] (O-A, "a1"*32)
    # before receipts[3] (O-A, "a2"*32). fetch_by_order_id must preserve that order.
    first_inserted = receipts[0]
    second_inserted = receipts[3]
    assert isinstance(first_inserted, SignedReceipt)
    assert isinstance(second_inserted, SignedReceipt)
    assert results[0].receipt_id == first_inserted.receipt_id, (
        "fetch_by_order_id must return receipts in insertion order: "
        f"expected {first_inserted.receipt_id!r} first, got {results[0].receipt_id!r}"
    )
    assert results[1].receipt_id == second_inserted.receipt_id, (
        "fetch_by_order_id must return receipts in insertion order: "
        f"expected {second_inserted.receipt_id!r} second, got {results[1].receipt_id!r}"
    )


def test_fetch_by_order_id_returns_empty_for_unknown_order(
    order_id_db: tuple[Path, list[object]],
) -> None:
    """fetch_by_order_id returns [] when no receipt matches the order_id."""
    from samantha_server.receipts.audit import fetch_by_order_id
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = order_id_db
    conn = _open_audit_conn(db_path)
    results = fetch_by_order_id(conn, "O-NONEXISTENT")
    conn.close()

    assert results == []


def test_fetch_by_order_id_does_not_return_null_order_id_receipts(
    order_id_db: tuple[Path, list[object]],
) -> None:
    """A receipt with order_id=None is not returned by fetch_by_order_id for any query."""
    from samantha_server.receipts.audit import fetch_by_order_id
    from samantha_server.receipts.store import _open_audit_conn

    db_path, receipts = order_id_db
    null_receipt = receipts[2]  # order_id=None receipt
    conn = _open_audit_conn(db_path)
    # fetch_by_order_id("O-A") must not include the None receipt
    results_a = fetch_by_order_id(conn, "O-A")
    conn.close()

    receipt_ids_a = {r.receipt_id for r in results_a}
    assert null_receipt.receipt_id not in receipt_ids_a  # type: ignore[union-attr]
