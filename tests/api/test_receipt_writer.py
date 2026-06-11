"""Tests for ReceiptWriter — fetch_payload_json and close().

Coverage:
- fetch_payload_json returns stored payload_json for a known receipt_id
- fetch_payload_json returns None for an unknown receipt_id
- fetch_payload_json raises ReceiptPersistenceError (not RuntimeError/AttributeError) after close()
- write_signed raises ReceiptPersistenceError (not AttributeError) after close()
- close() is idempotent: double-close is a no-op
- close() logs a WARNING with exc_info when conn.close() raises
- close() sets _conn to None even when conn.close() raises
- close() does not raise when conn.close() raises
- ReceiptWriter.open() routes through store._open_write_conn
- Connection from ReceiptWriter.open() has PRAGMA synchronous=NORMAL and foreign_keys=ON
- Connection from ReceiptWriter.open() allows cross-thread use
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from samantha_server.receipts.store import _SCHEMA_SQL


def _make_write_conn() -> sqlite3.Connection:
    """Return an in-memory write connection with the receipts schema applied."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    return conn


def _make_signed_receipt() -> object:
    """Return a freshly signed SignedReceipt with non-empty primitive_traces."""
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision
    from samantha_server.primitives.trace import PrimitiveTrace
    from samantha_server.receipts.signing import sign_decision

    decision = EngineDecision(
        applied_rule_id="ACC-008",
        next_state="ACCEPTED",
        flags_added=(),
        flags_cleared=(),
        outcome="accepted",
        also_matched=(),
        dispatched_rule_ids=("ACC-008",),
        event_input_hash="a" * 64,
        primitive_traces={
            "ACC-008": PrimitiveTrace(
                primitive="Equals",
                field="specimen_type",
                expected="biopsy",
                actual="biopsy",
                result=True,
            )
        },
        latency_us=100,
    )
    key = bytes(nacl.signing.SigningKey.generate())
    return sign_decision(decision, key_id="v1", signing_key=key)


# ---------------------------------------------------------------------------
# fetch_payload_json — returns stored payload for a known receipt_id
# ---------------------------------------------------------------------------


def test_fetch_payload_json_returns_payload_for_known_receipt() -> None:
    """fetch_payload_json returns the stored payload_json for an inserted receipt."""
    from samantha_server.api.receipt_writer import ReceiptWriter

    conn = _make_write_conn()
    writer = ReceiptWriter(conn)

    receipt = _make_signed_receipt()
    writer.write_signed(receipt)  # type: ignore[arg-type]

    result = writer.fetch_payload_json(receipt.receipt_id)  # type: ignore[union-attr]

    assert result is not None, "fetch_payload_json must return payload for a known receipt_id"
    parsed = json.loads(result)
    assert "next_state" in parsed, (
        "payload_json must deserialize to an EngineDecision-compatible dict"
    )
    assert parsed["next_state"] == "ACCEPTED"


# ---------------------------------------------------------------------------
# fetch_payload_json — returns None for an unknown receipt_id
# ---------------------------------------------------------------------------


def test_fetch_payload_json_returns_none_for_unknown_receipt() -> None:
    """fetch_payload_json returns None when receipt_id is not in the store."""
    from samantha_server.api.receipt_writer import ReceiptWriter

    conn = _make_write_conn()
    writer = ReceiptWriter(conn)

    result = writer.fetch_payload_json("NONEXISTENT-RECEIPT-ID")

    assert result is None, "fetch_payload_json must return None for an unknown receipt_id"


# ---------------------------------------------------------------------------
# fetch_payload_json — raises ReceiptPersistenceError after close()
# ---------------------------------------------------------------------------


def test_fetch_payload_json_raises_receipt_persistence_error_after_close() -> None:
    """fetch_payload_json raises ReceiptPersistenceError (not RuntimeError) after close().

    Upgraded from RuntimeError to ReceiptPersistenceError to align with
    the project typed-error hierarchy.
    """
    from samantha_server.api.receipt_writer import ReceiptWriter
    from samantha_server.errors import ReceiptPersistenceError

    conn = _make_write_conn()
    writer = ReceiptWriter(conn)
    writer.close()

    with pytest.raises(ReceiptPersistenceError):
        writer.fetch_payload_json("any-receipt-id")


# ---------------------------------------------------------------------------
# close() — visibility when conn.close() raises
# ---------------------------------------------------------------------------


def test_close_logs_warning_with_exc_info_when_conn_close_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """close() emits a WARNING with exc_info when conn.close() raises.

    Under the old code conn.close() failure was silently suppressed by
    contextlib.suppress(Exception). The fix logs at WARNING with exc_info
    so WAL-checkpoint failures at shutdown are diagnosable.
    """
    from samantha_server.api.receipt_writer import ReceiptWriter

    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_conn.close.side_effect = OSError("disk error during close")
    writer = ReceiptWriter(mock_conn)

    with caplog.at_level(logging.WARNING, logger="samantha_server.api.receipt_writer"):
        writer.close()

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        r.exc_info is not None and isinstance(r.exc_info[1], OSError) for r in warning_records
    ), "close() must log a WARNING with exc_info[1] as OSError when conn.close() raises"


def test_close_sets_conn_to_none_even_when_conn_close_raises() -> None:
    """close() sets _conn to None even when conn.close() raises."""
    from samantha_server.api.receipt_writer import ReceiptWriter

    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_conn.close.side_effect = OSError("disk error during close")
    writer = ReceiptWriter(mock_conn)

    writer.close()

    assert not writer.is_open()


def test_close_does_not_raise_when_conn_close_raises() -> None:
    """close() does not propagate an exception when conn.close() raises.

    Suppression semantics are preserved: close() must never raise out of aclose().
    """
    from samantha_server.api.receipt_writer import ReceiptWriter

    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_conn.close.side_effect = OSError("disk error during close")
    writer = ReceiptWriter(mock_conn)

    # Must not raise
    writer.close()


# ---------------------------------------------------------------------------
# ReceiptWriter.open() routes through store._open_write_conn
# ---------------------------------------------------------------------------


def test_receipt_writer_open_routes_through_store_open_write_conn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ReceiptWriter.open() must call store._open_write_conn (not inline sqlite3.connect).

    The spy monkeypatches _open_write_conn in the receipt_writer module's
    import namespace to confirm the delegation path.
    """
    from unittest.mock import MagicMock

    import samantha_server.api.receipt_writer as rw_mod

    db_path = tmp_path / "test_open.db"

    sentinel_conn = MagicMock(spec=sqlite3.Connection)
    sentinel_conn.execute.return_value = MagicMock()

    spy_calls: list[tuple[object, dict[str, object]]] = []

    def _spy_open_write_conn(path: object, **kwargs: object) -> sqlite3.Connection:
        spy_calls.append((path, dict(kwargs)))
        return sentinel_conn  # type: ignore[return-value]

    monkeypatch.setattr(rw_mod, "init_store", lambda p: None)
    monkeypatch.setattr(rw_mod, "_open_write_conn", _spy_open_write_conn)

    writer = rw_mod.ReceiptWriter.open(db_path)

    assert len(spy_calls) == 1, (
        "ReceiptWriter.open() must call _open_write_conn exactly once"
    )
    # The writer's connection is used from to_thread workers,
    # so the delegation must pass check_same_thread=False explicitly.
    assert spy_calls[0][1].get("check_same_thread") is False
    assert writer._conn is sentinel_conn


def test_receipt_writer_open_conn_has_synchronous_normal(tmp_path: Path) -> None:
    """Connection opened by ReceiptWriter.open() has PRAGMA synchronous=1 (NORMAL)."""
    from samantha_server.api.receipt_writer import ReceiptWriter

    db_path = tmp_path / "test_pragma.db"
    writer = ReceiptWriter.open(db_path)  # type: ignore[arg-type]
    try:
        row = writer._conn.execute("PRAGMA synchronous").fetchone()  # type: ignore[union-attr]
        assert row is not None
        assert row[0] == 1, f"Expected synchronous=1 (NORMAL), got {row[0]}"
    finally:
        writer.close()


def test_receipt_writer_open_conn_has_foreign_keys_on(tmp_path: Path) -> None:
    """Connection opened by ReceiptWriter.open() has PRAGMA foreign_keys=1."""
    from samantha_server.api.receipt_writer import ReceiptWriter

    db_path = tmp_path / "test_fk.db"
    writer = ReceiptWriter.open(db_path)  # type: ignore[arg-type]
    try:
        row = writer._conn.execute("PRAGMA foreign_keys").fetchone()  # type: ignore[union-attr]
        assert row is not None
        assert row[0] == 1, f"Expected foreign_keys=1, got {row[0]}"
    finally:
        writer.close()


def test_receipt_writer_open_conn_allows_cross_thread_use(
    tmp_path: Path,
) -> None:
    """ReceiptWriter.open() must pass check_same_thread=False to sqlite3.connect.

    Verifies the connection can be used from a different thread without raising
    sqlite3.ProgrammingError, which is the observable effect of check_same_thread=True.
    """
    import threading

    from samantha_server.api.receipt_writer import ReceiptWriter

    db_path = tmp_path / "test_thread.db"
    writer = ReceiptWriter.open(db_path)  # type: ignore[arg-type]
    errors: list[Exception] = []

    def _use_from_thread() -> None:
        try:
            writer._conn.execute("SELECT 1").fetchone()  # type: ignore[union-attr]
        except Exception as exc:
            errors.append(exc)

    t = threading.Thread(target=_use_from_thread)
    t.start()
    t.join()

    writer.close()
    assert errors == [], f"Cross-thread connection use raised: {errors[0]}"


# ---------------------------------------------------------------------------
# write_signed after close raises ReceiptPersistenceError;
#               close() is idempotent (double-close is a no-op).
# ---------------------------------------------------------------------------


def test_write_signed_after_close_raises_receipt_persistence_error() -> None:
    """write_signed() after close() must raise ReceiptPersistenceError.

    Under the old code _conn is set to None after close(); write_signed() calls
    insert(self._conn, ...) which would raise AttributeError from a NoneType
    access. The fix adds an explicit guard that raises ReceiptPersistenceError
    (the project-typed error for persistence failures), consistent with the
    typed-error hierarchy.
    """
    from samantha_server.api.receipt_writer import ReceiptWriter
    from samantha_server.errors import ReceiptPersistenceError

    conn = _make_write_conn()
    writer = ReceiptWriter(conn)
    writer.close()

    receipt = _make_signed_receipt()
    with pytest.raises(ReceiptPersistenceError, match="[Ww]riter"):
        writer.write_signed(receipt)  # type: ignore[arg-type]


def test_is_open_returns_false_after_clean_close() -> None:
    """A clean close() flips is_open() to False.

    Pins the liveness-flag contract: is_open() is a flag (not a DB probe),
    set True at construction and flipped to False by close(). A dead-but-never-
    written connection reads True until the first write fails (
    trade-off, issue-preferred design).
    """
    from samantha_server.api.receipt_writer import ReceiptWriter

    conn = _make_write_conn()
    writer = ReceiptWriter(conn)
    assert writer.is_open()
    writer.close()
    assert not writer.is_open()


def test_close_is_idempotent() -> None:
    """Calling close() on an already-closed ReceiptWriter must be a no-op."""
    from samantha_server.api.receipt_writer import ReceiptWriter

    conn = _make_write_conn()
    writer = ReceiptWriter(conn)
    writer.close()
    # Second close must not raise
    writer.close()
    assert not writer.is_open()


# ---------------------------------------------------------------------------
# is_open() liveness flag
# (review fix #4)
# test_is_open_returns_false_after_close was merged with the existing
# test_is_open_returns_false_after_clean_close (same assertion, no drop in coverage).
# ---------------------------------------------------------------------------


def test_is_open_returns_true_for_open_writer() -> None:
    """is_open() returns True for a freshly opened writer."""
    from samantha_server.api.receipt_writer import ReceiptWriter

    conn = _make_write_conn()
    writer = ReceiptWriter(conn)
    assert writer.is_open() is True
    writer.close()


def test_is_open_returns_false_after_write_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """is_open() returns False after write_signed raises on insert.

    Simulates a write failure by making the connection's execute method raise;
    after such a failure the liveness flag must flip to False.
    Also asserts that the write failure is logged at WARNING level
    (corrupted-DB errors must be distinguishable from clean close).
    """
    from samantha_server.api.receipt_writer import ReceiptWriter
    from samantha_server.errors import ReceiptPersistenceError

    mock_conn = MagicMock(spec=sqlite3.Connection)
    mock_conn.execute.side_effect = sqlite3.DatabaseError("disk full")
    writer = ReceiptWriter(mock_conn)

    receipt = _make_signed_receipt()

    with (
        caplog.at_level(logging.WARNING, logger="samantha_server.api.receipt_writer"),
        pytest.raises(ReceiptPersistenceError),
    ):
        writer.write_signed(receipt)  # type: ignore[arg-type]

    assert not writer.is_open(), "is_open() must return False after write_signed fails"

    # Verify the failure was logged at WARNING (diagnostic requirement).
    warning_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "samantha_server.api.receipt_writer" in r.name
    ]
    assert warning_records, (
        "A write failure must produce at least one WARNING log on the receipt_writer logger"
    )
