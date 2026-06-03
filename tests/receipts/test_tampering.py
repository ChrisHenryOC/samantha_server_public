"""Tampering detection regression test.

Write a receipt to the store, then UPDATE payload_json directly via a raw
connection (bypassing the audit connection). Rehydrate via audit.fetch_by_event_hash
and call verify_signature → expect INVALID_SIGNATURE.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import nacl.signing
import pytest


@pytest.fixture
def tampered_db(tmp_path: Path) -> tuple[Path, object, bytes]:
    """Return (db_path, original_receipt, signing_key) after inserting and tampering.

    Tampers with a *valid* EngineDecision payload (different content) so that
    rehydration succeeds but signature verification catches the tampering.
    """
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import _canonical_signing_payload, sign_decision
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "tamper.db"
    init_store(db_path)

    decision = EngineDecision(
        applied_rule_id="ACC-001",
        next_state="HOLD",
        flags_added=(),
        flags_cleared=(),
        outcome="hold_missing_name",
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash="e" * 64,
        primitive_traces={},
        latency_us=7,
    )
    key = bytes(nacl.signing.SigningKey.generate())
    receipt = sign_decision(decision, key_id="v1", signing_key=key)

    conn = _open_write_conn(db_path)
    insert(conn, receipt)
    conn.close()

    # Tamper: use a raw connection (not audit conn) to corrupt payload_json
    # with a *different* valid EngineDecision so rehydration succeeds but
    # the signature no longer covers this payload.
    tampered_decision = EngineDecision(
        applied_rule_id="ACC-001",
        next_state="ACCEPTED",  # attacker changed this
        flags_added=(),
        flags_cleared=(),
        outcome="accepted_tampered",  # attacker changed this
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash="e" * 64,
        primitive_traces={},
        latency_us=7,
    )
    tampered_payload = _canonical_signing_payload(tampered_decision).decode()
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.execute(
        "UPDATE receipts SET payload_json = ?, next_state = ?, outcome = ? WHERE receipt_id = ?",
        (tampered_payload, "ACCEPTED", "accepted_tampered", receipt.receipt_id),
    )
    raw_conn.commit()
    raw_conn.close()

    return db_path, receipt, key


def test_tampered_payload_returns_invalid_signature(
    tampered_db: tuple[Path, object, bytes],
) -> None:
    """Tampered payload_json causes verify_signature to return INVALID_SIGNATURE.

    Exercises the full store → audit → verify path:
    1. Receipt written via the store (honest write)
    2. payload_json column mutated via raw SQL to a different valid EngineDecision
    3. Rehydration via audit.fetch_by_event_hash succeeds (valid JSON)
    4. verify_signature sees the tampered payload → INVALID_SIGNATURE
    """
    from samantha_server.receipts.audit import fetch_by_event_hash, verify_signature
    from samantha_server.receipts.signing import VerificationResult
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _original_receipt, key = tampered_db

    audit_conn = _open_audit_conn(db_path)
    results = fetch_by_event_hash(audit_conn, "e" * 64)
    audit_conn.close()

    assert len(results) == 1
    rehydrated = results[0]

    result = verify_signature(rehydrated, current_key=key, current_key_id="v1")
    assert result == VerificationResult.INVALID_SIGNATURE


def test_store_tampering_via_raw_sql_is_detectable(tmp_path: Path) -> None:
    """Write a receipt, corrupt payload_json via raw SQL, verify → INVALID_SIGNATURE.

    This test exercises the full store → audit → verify path.
    The payload_json corruption means the stored data no longer matches
    the signature computed over the original canonical payload.
    """
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.audit import fetch_by_event_hash, verify_signature
    from samantha_server.receipts.signing import (
        VerificationResult,
        _canonical_signing_payload,
        sign_decision,
    )
    from samantha_server.receipts.store import (
        _open_audit_conn,
        _open_write_conn,
        init_store,
        insert,
    )

    db_path = tmp_path / "detect_tamper.db"
    init_store(db_path)

    key = bytes(nacl.signing.SigningKey.generate())

    decision = EngineDecision(
        applied_rule_id="ACC-001",
        next_state="HOLD",
        flags_added=(),
        flags_cleared=(),
        outcome="hold_missing_name",
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash="h" * 64,
        primitive_traces={},
        latency_us=3,
    )
    receipt = sign_decision(decision, key_id="v1", signing_key=key)

    # Insert honestly
    write_conn = _open_write_conn(db_path)
    insert(write_conn, receipt)
    write_conn.close()

    # Tamper payload_json directly via raw connection
    raw_conn = sqlite3.connect(str(db_path))
    # Replace payload with a slightly different JSON that decodes to a valid
    # EngineDecision but with a different outcome (what an attacker would want)
    tampered_decision = EngineDecision(
        applied_rule_id="ACC-001",
        next_state="ACCEPTED",  # attacker changed this
        flags_added=(),
        flags_cleared=(),
        outcome="accepted_tampered",  # attacker changed this
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash="h" * 64,
        primitive_traces={},
        latency_us=3,
    )
    raw_conn.execute(
        "UPDATE receipts SET payload_json = ?, next_state = ?, outcome = ? WHERE receipt_id = ?",
        (
            _canonical_signing_payload(tampered_decision).decode(),
            "ACCEPTED",
            "accepted_tampered",
            receipt.receipt_id,
        ),
    )
    raw_conn.commit()
    raw_conn.close()

    # Now rehydrate via audit and verify
    audit_conn = _open_audit_conn(db_path)
    results = fetch_by_event_hash(audit_conn, "h" * 64)
    audit_conn.close()

    assert len(results) == 1
    rehydrated = results[0]

    # The rehydrated receipt has the tampered decision but original signature
    result = verify_signature(rehydrated, current_key=key, current_key_id="v1")
    assert result == VerificationResult.INVALID_SIGNATURE
