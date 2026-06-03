"""Tests for ReceiptWriter.fetch_payload_json (GH-324 review finding).

Coverage:
- fetch_payload_json returns stored payload_json for a known receipt_id
- fetch_payload_json returns None for an unknown receipt_id
- fetch_payload_json raises RuntimeError (not bare AttributeError) after close()
"""

from __future__ import annotations

import json
import sqlite3

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
# fetch_payload_json — raises RuntimeError after close(), not AttributeError
# ---------------------------------------------------------------------------


def test_fetch_payload_json_raises_runtime_error_after_close() -> None:
    """fetch_payload_json raises RuntimeError (not bare AttributeError) after close()."""
    from samantha_server.api.receipt_writer import ReceiptWriter

    conn = _make_write_conn()
    writer = ReceiptWriter(conn)
    writer.close()

    with pytest.raises(RuntimeError, match="ReceiptWriter is closed"):
        writer.fetch_payload_json("any-receipt-id")
