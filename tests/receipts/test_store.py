"""Tests for samantha_server.receipts.store (Phase 2 Step 6)."""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signed_receipt() -> object:
    """Return a freshly signed minimal SignedReceipt for store tests."""
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import sign_decision

    decision = EngineDecision(
        applied_rule_id="ACC-001",
        next_state="HOLD",
        flags_added=(),
        flags_cleared=(),
        outcome="hold_missing_name",
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash="b" * 64,
        primitive_traces={},
        latency_us=99,
    )
    key = bytes(nacl.signing.SigningKey.generate())
    return sign_decision(decision, key_id="v1", signing_key=key)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return a path for a fresh SQLite receipts DB in a temp dir."""
    return tmp_path / "receipts.db"


# ---------------------------------------------------------------------------
# init_store
# ---------------------------------------------------------------------------


def test_init_store_creates_file(db_path: Path) -> None:
    from samantha_server.receipts.store import init_store

    assert not db_path.exists()
    init_store(db_path)
    assert db_path.exists()


def test_init_store_sets_mode_0o600(db_path: Path) -> None:
    from samantha_server.receipts.store import init_store

    init_store(db_path)
    file_mode = stat.S_IMODE(db_path.stat().st_mode)
    assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"


def test_init_store_is_idempotent(db_path: Path) -> None:
    """Calling init_store twice on the same path does not raise or delete data.

    L-05: also inserts a receipt between the two calls and verifies it survives.
    """
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    init_store(db_path)

    receipt = _make_signed_receipt()
    conn = _open_write_conn(db_path)
    insert(conn, receipt)  # type: ignore[arg-type]
    conn.close()

    init_store(db_path)  # second call — must not delete existing data
    assert db_path.exists()

    # Verify the receipt inserted before the second init_store still exists
    verify_conn = _open_write_conn(db_path)
    rows = verify_conn.execute(
        "SELECT receipt_id FROM receipts WHERE receipt_id=?",
        (receipt.receipt_id,),  # type: ignore[union-attr]
    ).fetchall()
    verify_conn.close()
    assert len(rows) == 1, "Receipt did not survive the second init_store call"


def test_init_store_creates_receipts_table(db_path: Path) -> None:
    from samantha_server.receipts.store import init_store

    init_store(db_path)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='receipts'")
    row = cursor.fetchone()
    conn.close()
    assert row is not None, "Expected 'receipts' table to be created"


def test_init_store_wal_mode(db_path: Path) -> None:
    from samantha_server.receipts.store import init_store

    init_store(db_path)
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("PRAGMA journal_mode").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "wal", f"Expected WAL mode, got {row[0]!r}"


def test_init_store_rejects_dotdot_traversal(tmp_path: Path) -> None:
    """Paths containing '..' should be rejected."""
    from samantha_server.receipts.store import init_store

    traversal_path = tmp_path / ".." / "evil.db"
    with pytest.raises((ValueError, OSError)):
        init_store(traversal_path)


def test_validate_store_path_rejects_symlink_escape(tmp_path: Path) -> None:
    """H-12: A symlink pointing outside the allowed_base directory must be rejected.

    _validate_store_path resolves symlinks before checking relative_to(allowed_base),
    so a symlink pointing outside the allowed base raises ValueError.
    """
    from samantha_server.receipts.store import _validate_store_path

    allowed_base = tmp_path  # only paths under tmp_path are allowed
    # Symlink points outside allowed_base
    link = tmp_path / "link.db"
    link.symlink_to(str(tmp_path.parent / "outside.db"))

    with pytest.raises(ValueError):
        _validate_store_path(link, allowed_base=allowed_base)


def test_init_store_raises_on_unwriteable_parent(tmp_path: Path) -> None:
    """M-24: init_store on a non-writeable parent dir raises ReceiptPersistenceError."""
    import stat

    from samantha_server.errors import ReceiptPersistenceError
    from samantha_server.receipts.store import init_store

    locked_dir = tmp_path / "locked"
    locked_dir.mkdir()
    locked_dir.chmod(stat.S_IREAD | stat.S_IEXEC)  # remove write bit
    try:
        db_path = locked_dir / "test.db"
        with pytest.raises((ReceiptPersistenceError, PermissionError, OSError)):
            init_store(db_path)
    finally:
        locked_dir.chmod(0o755)  # restore so cleanup can proceed


# ---------------------------------------------------------------------------
# insert + round-trip
# ---------------------------------------------------------------------------


def test_insert_round_trips_receipt(db_path: Path) -> None:
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    init_store(db_path)
    receipt = _make_signed_receipt()
    assert isinstance(receipt, SignedReceipt)

    conn = _open_write_conn(db_path)
    insert(conn, receipt)
    conn.commit()

    rows = conn.execute("SELECT * FROM receipts").fetchall()
    conn.close()

    assert len(rows) == 1
    row = rows[0]
    assert row[0] == receipt.receipt_id


def test_insert_round_trips_signature_bytes(db_path: Path) -> None:
    """H-13: The 64-byte signature BLOB round-trips correctly through insert/fetch."""
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    init_store(db_path)
    receipt = _make_signed_receipt()
    assert isinstance(receipt, SignedReceipt)

    conn = _open_write_conn(db_path)
    insert(conn, receipt)
    conn.commit()

    row = conn.execute(
        "SELECT signature FROM receipts WHERE receipt_id=?", (receipt.receipt_id,)
    ).fetchone()
    conn.close()

    assert row is not None
    assert bytes(row[0]) == receipt.signature, (
        "Signature BLOB does not match original receipt.signature — "
        "the integrity anchor was corrupted in storage."
    )


def test_insert_stores_event_input_hash(db_path: Path) -> None:
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    init_store(db_path)
    receipt = _make_signed_receipt()
    assert isinstance(receipt, SignedReceipt)

    conn = _open_write_conn(db_path)
    insert(conn, receipt)
    conn.commit()

    row = conn.execute(
        "SELECT event_input_hash FROM receipts WHERE receipt_id=?", (receipt.receipt_id,)
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == receipt.decision.event_input_hash


def test_insert_duplicate_receipt_id_raises_persistence_error(db_path: Path) -> None:
    """M-24: duplicate receipt_id (PK violation) maps to ReceiptPersistenceError."""
    from samantha_server.errors import ReceiptPersistenceError
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    init_store(db_path)
    receipt = _make_signed_receipt()
    assert isinstance(receipt, SignedReceipt)

    conn = _open_write_conn(db_path)
    insert(conn, receipt)  # first insert succeeds

    with pytest.raises(ReceiptPersistenceError):
        insert(conn, receipt)  # duplicate PK → IntegrityError → ReceiptPersistenceError
    conn.close()


def test_insert_closed_connection_raises_persistence_error(db_path: Path) -> None:
    """M-24: inserting into a closed connection raises ReceiptPersistenceError."""
    from samantha_server.errors import ReceiptPersistenceError
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    init_store(db_path)
    receipt = _make_signed_receipt()
    assert isinstance(receipt, SignedReceipt)

    conn = _open_write_conn(db_path)
    conn.close()  # close before insert

    with pytest.raises(ReceiptPersistenceError):
        insert(conn, receipt)


def test_payload_json_column_equals_canonical_signing_payload(db_path: Path) -> None:
    """H-01: payload_json column must equal _canonical_signing_payload(decision).decode().

    The stored column must be the same bytes that were signed so that any
    future tool hashing or verifying the column directly produces correct results.
    """
    from samantha_server.receipts.signing import SignedReceipt, _canonical_signing_payload
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    init_store(db_path)
    receipt = _make_signed_receipt()
    assert isinstance(receipt, SignedReceipt)

    conn = _open_write_conn(db_path)
    insert(conn, receipt)

    row = conn.execute(
        "SELECT payload_json FROM receipts WHERE receipt_id=?", (receipt.receipt_id,)
    ).fetchone()
    conn.close()

    assert row is not None
    expected = _canonical_signing_payload(receipt.decision).decode()
    assert row[0] == expected, (
        "payload_json column does not match _canonical_signing_payload(decision).decode(); "
        "the stored artifact is not independently verifiable."
    )


def test_insert_stores_payload_json(db_path: Path) -> None:
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    init_store(db_path)
    receipt = _make_signed_receipt()
    assert isinstance(receipt, SignedReceipt)

    conn = _open_write_conn(db_path)
    insert(conn, receipt)
    conn.commit()

    row = conn.execute(
        "SELECT payload_json FROM receipts WHERE receipt_id=?", (receipt.receipt_id,)
    ).fetchone()
    conn.close()

    assert row is not None
    # payload_json should be valid JSON that round-trips back to EngineDecision
    from samantha_server.engine.decision import EngineDecision

    d = EngineDecision.model_validate_json(row[0])
    assert d.event_input_hash == receipt.decision.event_input_hash


# ---------------------------------------------------------------------------
# Audit connection — query_only
# ---------------------------------------------------------------------------


def test_audit_conn_is_query_only(db_path: Path) -> None:
    from samantha_server.receipts.store import _open_audit_conn, init_store

    init_store(db_path)
    conn = _open_audit_conn(db_path)

    row = conn.execute("PRAGMA query_only").fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 1, f"Expected query_only=1, got {row[0]}"


def test_audit_conn_rejects_update(db_path: Path) -> None:
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import (
        _open_audit_conn,
        _open_write_conn,
        init_store,
        insert,
    )

    init_store(db_path)
    receipt = _make_signed_receipt()
    assert isinstance(receipt, SignedReceipt)

    write_conn = _open_write_conn(db_path)
    insert(write_conn, receipt)
    write_conn.commit()
    write_conn.close()

    # Audit connection should reject UPDATE
    audit_conn = _open_audit_conn(db_path)
    with pytest.raises(sqlite3.OperationalError):
        audit_conn.execute(
            "UPDATE receipts SET outcome='tampered' WHERE receipt_id=?",
            (receipt.receipt_id,),
        )
    audit_conn.close()


def test_commit_failure_raises_receipt_persistence_error(db_path: Path) -> None:
    """C-01: sqlite3.OperationalError during conn.commit() must raise ReceiptPersistenceError.

    Simulates a disk-full / SQLITE_IOERR condition by wrapping conn.commit()
    in insert() (after the fix) so commit failures are mapped to
    ReceiptPersistenceError instead of escaping as raw OperationalError.
    """

    from samantha_server.errors import ReceiptPersistenceError
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    init_store(db_path)
    receipt = _make_signed_receipt()
    assert isinstance(receipt, SignedReceipt)

    conn = _open_write_conn(db_path)

    # Use a wrapper object that intercepts commit() to raise OperationalError
    class _FaultyConn:
        """Thin wrapper delegating all calls to conn except commit() which raises."""

        def __getattr__(self, name: str) -> object:
            return getattr(conn, name)

        def commit(self) -> None:
            raise sqlite3.OperationalError("disk full")

    with pytest.raises(ReceiptPersistenceError, match="disk full"):
        insert(_FaultyConn(), receipt)  # type: ignore[arg-type]
    conn.close()


def test_audit_conn_rejects_delete(db_path: Path) -> None:
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import (
        _open_audit_conn,
        _open_write_conn,
        init_store,
        insert,
    )

    init_store(db_path)
    receipt = _make_signed_receipt()
    assert isinstance(receipt, SignedReceipt)

    write_conn = _open_write_conn(db_path)
    insert(write_conn, receipt)
    write_conn.commit()
    write_conn.close()

    audit_conn = _open_audit_conn(db_path)
    with pytest.raises(sqlite3.OperationalError):
        audit_conn.execute(
            "DELETE FROM receipts WHERE receipt_id=?",
            (receipt.receipt_id,),
        )
    audit_conn.close()


# ---------------------------------------------------------------------------
# H-04: _get_write_conn lives in store.py; the router delegates here.
# Smoke test: two calls return the same connection object.
# ---------------------------------------------------------------------------


def test_get_write_conn_returns_same_connection(
    receipts_test_isolation: None,
) -> None:
    """_get_write_conn returns the same sqlite3.Connection on repeated calls.

    H-04: the connection cache is owned by store.py. The router calls
    store._get_write_conn; this test confirms idempotency.
    """
    from samantha_server.receipts.store import _get_write_conn

    conn1 = _get_write_conn()
    conn2 = _get_write_conn()
    assert conn1 is conn2, (
        "_get_write_conn must return the same connection object on repeated calls "
        "(module-level cache check)"
    )


# ---------------------------------------------------------------------------
# fetch_by_receipt_id — read helper for replay harness (GH-333 PHI fix)
# ---------------------------------------------------------------------------


def test_fetch_by_receipt_id_returns_payload_json(tmp_path: Path) -> None:
    """fetch_by_receipt_id returns the payload_json bytes for a persisted receipt.

    The payload_json round-trips through EngineDecision.model_validate losslessly,
    preserving decision_traces and primitive_traces (which the /events wire omits).
    """
    import json

    import nacl.signing

    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import sign_decision
    from samantha_server.receipts.store import (
        _open_write_conn,
        fetch_by_receipt_id,
        init_store,
        insert,
    )

    db_path = tmp_path / "test_fetch.db"
    init_store(db_path)

    decision = EngineDecision(
        applied_rule_id="ACC-001",
        next_state="ACCEPTED",
        flags_added=(),
        flags_cleared=(),
        outcome="accepted",
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash="a" * 64,
        primitive_traces={},
        latency_us=42,
    )
    key = bytes(nacl.signing.SigningKey.generate())
    receipt = sign_decision(decision, key_id="v1", signing_key=key)

    conn = _open_write_conn(db_path)
    insert(conn, receipt)
    conn.close()

    read_conn = _open_write_conn(db_path)
    try:
        payload_json = fetch_by_receipt_id(read_conn, receipt.receipt_id)
    finally:
        read_conn.close()

    assert payload_json is not None, "fetch_by_receipt_id must return payload_json for known id"
    parsed = json.loads(payload_json)
    reconstructed = EngineDecision.model_validate(parsed)
    assert reconstructed.next_state == decision.next_state
    assert reconstructed.applied_rule_id == decision.applied_rule_id


def test_fetch_by_receipt_id_returns_none_for_missing(tmp_path: Path) -> None:
    """fetch_by_receipt_id returns None when the receipt_id is not in the store."""
    from samantha_server.receipts.store import (
        _open_write_conn,
        fetch_by_receipt_id,
        init_store,
    )

    db_path = tmp_path / "test_fetch_miss.db"
    init_store(db_path)
    conn = _open_write_conn(db_path)
    try:
        result = fetch_by_receipt_id(conn, "NONEXISTENT-RECEIPT-ID")
    finally:
        conn.close()

    assert result is None, "fetch_by_receipt_id must return None for unknown receipt_id"


def test_fetch_by_receipt_id_preserves_decision_traces(tmp_path: Path) -> None:
    """payload_json from fetch_by_receipt_id includes decision_traces when present.

    The replay harness reads decision_traces from the persisted receipt (not the
    PHI-stripped /events wire); this test confirms round-trip fidelity.
    """
    import json

    import nacl.signing

    from samantha_server.engine.decision import EngineDecision, QueryTrace
    from samantha_server.receipts.signing import sign_decision
    from samantha_server.receipts.store import (
        _open_write_conn,
        fetch_by_receipt_id,
        init_store,
        insert,
    )

    db_path = tmp_path / "test_fetch_traces.db"
    init_store(db_path)

    trace = QueryTrace(
        query_text_hash="q" * 64,
        skill_doc_hash="s" * 64,
        scenarios_cited=("SC-001",),
        model_id="test-model",
        response_text_hash="r" * 64,
    )
    decision = EngineDecision(
        applied_rule_id=None,
        next_state="ACCEPTED",
        flags_added=(),
        flags_cleared=(),
        outcome="llm_routed",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="c" * 64,
        primitive_traces={},
        latency_us=100,
        decision_traces=(trace,),
    )
    key = bytes(nacl.signing.SigningKey.generate())
    receipt = sign_decision(decision, key_id="v1", signing_key=key)

    conn = _open_write_conn(db_path)
    insert(conn, receipt)
    conn.close()

    read_conn = _open_write_conn(db_path)
    try:
        payload_json = fetch_by_receipt_id(read_conn, receipt.receipt_id)
    finally:
        read_conn.close()

    assert payload_json is not None
    parsed = json.loads(payload_json)
    reconstructed = EngineDecision.model_validate(parsed)
    assert len(reconstructed.decision_traces) == 1, (
        "decision_traces must survive the payload_json round-trip"
    )
    assert isinstance(reconstructed.decision_traces[0], QueryTrace)


# ---------------------------------------------------------------------------
# GH-367: order_id column on receipts table
# ---------------------------------------------------------------------------


def _make_signed_receipt_with_order_id(order_id: str | None) -> object:
    """Return a freshly signed SignedReceipt with decision.order_id set."""
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
        event_input_hash="d" * 64,
        primitive_traces={},
        latency_us=10,
        order_id=order_id,
    )
    key = bytes(nacl.signing.SigningKey.generate())
    return sign_decision(decision, key_id="v1", signing_key=key)


def test_insert_stores_order_id_column(db_path: Path) -> None:
    """insert() persists decision.order_id into the order_id column."""
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    init_store(db_path)
    receipt = _make_signed_receipt_with_order_id("O-STORE-001")
    conn = _open_write_conn(db_path)
    insert(conn, receipt)  # type: ignore[arg-type]
    conn.commit()

    row = conn.execute(
        "SELECT order_id FROM receipts WHERE receipt_id = ?",
        (receipt.receipt_id,),  # type: ignore[union-attr]
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "O-STORE-001"


def test_insert_stores_null_order_id_when_none(db_path: Path) -> None:
    """insert() stores NULL in order_id when decision.order_id is None."""
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    init_store(db_path)
    receipt = _make_signed_receipt_with_order_id(None)
    conn = _open_write_conn(db_path)
    insert(conn, receipt)  # type: ignore[arg-type]
    conn.commit()

    row = conn.execute(
        "SELECT order_id FROM receipts WHERE receipt_id = ?",
        (receipt.receipt_id,),  # type: ignore[union-attr]
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] is None


def test_init_schema_idempotent_alter_is_safe_to_run_twice(db_path: Path) -> None:
    """init_schema called twice on a DB that already has order_id column does not raise.

    This covers the idempotent ALTER TABLE migration path: the second call
    should swallow the 'duplicate column name' OperationalError.
    """
    import sqlite3

    from samantha_server.receipts.store import init_schema, init_store

    init_store(db_path)
    conn = sqlite3.connect(str(db_path))
    # Call init_schema twice — must be idempotent.
    init_schema(conn)
    init_schema(conn)
    conn.close()


def test_init_schema_migrates_legacy_db_missing_order_id_column(db_path: Path) -> None:
    """GH-367: init_schema() on a pre-existing DB lacking order_id must add the column.

    Mirrors test_init_store_migrates_legacy_db_missing_order_id_column but drives
    init_schema(conn) instead of init_store(path). The replay harness and parity CLI
    call init_schema directly with an open connection, so this path must also apply
    the ALTER migration before executescript — otherwise the CREATE INDEX on order_id
    raises OperationalError inside executescript before the ALTER can run.

    Failure mode without the fix: sqlite3.OperationalError: no such column: order_id
    raised from executescript() before control reaches the ALTER TABLE statement.
    """
    import sqlite3

    from samantha_server.receipts.store import _open_write_conn, init_schema, insert

    # Arrange: build a legacy DB (no order_id column).
    legacy_conn = sqlite3.connect(str(db_path))
    legacy_conn.execute("PRAGMA journal_mode=WAL")
    legacy_conn.execute(
        """
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
        ) STRICT
        """
    )
    legacy_conn.commit()
    legacy_conn.close()

    # Act: call init_schema with a fresh open connection on the legacy DB.
    schema_conn = sqlite3.connect(str(db_path))
    init_schema(schema_conn)
    schema_conn.close()

    # Assert: order_id column now exists (PRAGMA table_info lists it).
    verify_conn = sqlite3.connect(str(db_path))
    columns = [row[1] for row in verify_conn.execute("PRAGMA table_info(receipts)").fetchall()]
    verify_conn.close()
    assert "order_id" in columns, (
        "init_schema must add the order_id column to a legacy DB that was created without it"
    )

    # Assert: can insert a receipt with order_id set (proves column is writable).
    receipt = _make_signed_receipt_with_order_id("O-SCHEMA-001")
    write_conn = _open_write_conn(db_path)
    insert(write_conn, receipt)  # type: ignore[arg-type]
    row = write_conn.execute(
        "SELECT order_id FROM receipts WHERE receipt_id = ?",
        (receipt.receipt_id,),  # type: ignore[union-attr]
    ).fetchone()
    write_conn.close()
    assert row is not None
    assert row[0] == "O-SCHEMA-001"


# ---------------------------------------------------------------------------
# FIX 1: _migrate_add_order_id_column — narrowed error swallowing
# ---------------------------------------------------------------------------


def test_migrate_add_order_id_column_swallows_duplicate_column_error(tmp_path: Path) -> None:
    """_migrate_add_order_id_column swallows OperationalError for 'duplicate column name'.

    Confirms the expected idempotent path: when the column already exists the
    duplicate-column-name OperationalError is silently suppressed and the
    function returns normally.
    """
    import sqlite3

    from samantha_server.receipts.store import _migrate_add_order_id_column

    class _StubConn:
        """Stub connection whose execute raises the duplicate-column OperationalError."""

        def execute(self, sql: str) -> None:  # type: ignore[override]
            raise sqlite3.OperationalError("duplicate column name: order_id")

    _migrate_add_order_id_column(_StubConn())  # type: ignore[arg-type]
    # If we reach here the error was swallowed — test passes.


def test_migrate_add_order_id_column_reraises_non_duplicate_error(tmp_path: Path) -> None:
    """_migrate_add_order_id_column re-raises OperationalError that is NOT duplicate-column.

    Confirms the silent-failure-hunter fix: errors like 'database is locked'
    must not be masked. Only the specific 'duplicate column name' text is
    swallowed.
    """
    import sqlite3

    import pytest

    from samantha_server.receipts.store import _migrate_add_order_id_column

    class _LockedConn:
        """Stub connection whose execute raises a locked-DB OperationalError."""

        def execute(self, sql: str) -> None:  # type: ignore[override]
            raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        _migrate_add_order_id_column(_LockedConn())  # type: ignore[arg-type]


def test_init_store_migrates_legacy_db_missing_order_id_column(db_path: Path) -> None:
    """GH-367: init_store() on a pre-existing DB lacking order_id must add the column.

    Simulates the convex-incumbent DB scenario: a receipts DB created before the
    order_id column was added to schema.sql. The production write path goes through
    init_store (not init_schema), so init_store must apply the ALTER migration.

    Failure mode without the fix: sqlite3.OperationalError: table receipts has no
    column named order_id on the subsequent insert().
    """
    import sqlite3

    from samantha_server.receipts.audit import fetch_by_order_id
    from samantha_server.receipts.store import (
        _open_audit_conn,
        _open_write_conn,
        init_store,
        insert,
    )

    # Arrange: create a legacy DB with the old schema (no order_id column).
    # Use a minimal CREATE TABLE that mirrors what the old schema.sql produced.
    legacy_conn = sqlite3.connect(str(db_path))
    legacy_conn.execute("PRAGMA journal_mode=WAL")
    legacy_conn.execute(
        """
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
        ) STRICT
        """
    )
    legacy_conn.commit()
    legacy_conn.close()
    db_path.chmod(0o600)

    # Act: call init_store (the production path) on the legacy DB.
    init_store(db_path)

    # Act: insert a receipt with a non-None order_id via the production write path.
    receipt = _make_signed_receipt_with_order_id("O-LEGACY-001")
    write_conn = _open_write_conn(db_path)
    insert(write_conn, receipt)  # type: ignore[arg-type]
    write_conn.close()

    # Assert: fetch_by_order_id finds the receipt.
    audit_conn = _open_audit_conn(db_path)
    results = fetch_by_order_id(audit_conn, "O-LEGACY-001")
    audit_conn.close()
    assert len(results) == 1, (
        "fetch_by_order_id must return the inserted receipt for the legacy DB "
        "after init_store migrates the missing order_id column"
    )
