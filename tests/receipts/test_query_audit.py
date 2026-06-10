"""Slice 7: Tests for audit.fetch_by_query_text_hash."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_query_receipt(
    query_text_hash: str,
    event_hash: str | None = None,
) -> object:
    """Return a signed receipt with outcome='query_response' and a QueryTrace."""
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision, QueryTrace
    from samantha_server.receipts.signing import sign_decision

    if event_hash is None:
        event_hash = hashlib.sha256(query_text_hash.encode()).hexdigest()

    decision = EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash=event_hash,
        primitive_traces={},
        latency_us=0,
        decision_traces=(
            QueryTrace(
                query_text_hash=query_text_hash,
                skill_doc_hash="abc" * 21 + "a",  # 64-char hex-ish dummy
                scenarios_cited=("QR-001",),
                model_id="test-model",
                response_text_hash="def" * 21 + "a",
            ),
        ),
    )
    key = bytes(nacl.signing.SigningKey.generate())
    return sign_decision(decision, key_id="v1", signing_key=key)


def _make_non_query_receipt(event_hash: str = "a" * 64) -> object:
    """Return a receipt with outcome != 'query_response' (no QueryTrace)."""
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import sign_decision

    decision = EngineDecision(
        applied_rule_id="ACC-001",
        next_state="MISSING_INFO_HOLD",
        flags_added=(),
        flags_cleared=(),
        outcome="hold_missing_name",
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash=event_hash,
        primitive_traces={},
        latency_us=10,
    )
    key = bytes(nacl.signing.SigningKey.generate())
    return sign_decision(decision, key_id="v1", signing_key=key)


@pytest.fixture
def query_db(tmp_path: Path) -> tuple[Path, list[object]]:
    """Return (db_path, [receipts]) with mixed query and non-query receipts."""
    from samantha_server.receipts.store import _open_write_conn, init_store, insert

    db_path = tmp_path / "query_receipts.db"
    init_store(db_path)

    qhash_a = "a" * 64
    qhash_b = "b" * 64

    receipts = [
        _make_query_receipt(query_text_hash=qhash_a, event_hash="e1" + "c" * 62),
        _make_query_receipt(query_text_hash=qhash_a, event_hash="e2" + "c" * 62),
        _make_query_receipt(query_text_hash=qhash_b, event_hash="e3" + "c" * 62),
        _make_non_query_receipt(event_hash="e4" + "c" * 62),
    ]

    conn = _open_write_conn(db_path)
    for r in receipts:
        insert(conn, r)  # type: ignore[arg-type]
    conn.commit()
    conn.close()

    return db_path, receipts


# ---------------------------------------------------------------------------
# Slice 7: fetch_by_query_text_hash
# ---------------------------------------------------------------------------


def test_fetch_by_query_text_hash_returns_matching_queries(
    query_db: tuple[Path, list[object]],
) -> None:
    """fetch_by_query_text_hash returns only receipts matching the query_text_hash."""
    from samantha_server.receipts.audit import fetch_by_query_text_hash
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = query_db
    conn = _open_audit_conn(db_path)
    results = fetch_by_query_text_hash(conn, "a" * 64)
    conn.close()

    assert len(results) == 2
    for r in results:
        assert isinstance(r, SignedReceipt)
        assert r.decision.outcome == "query_response"


def test_fetch_by_query_text_hash_filters_by_hash(
    query_db: tuple[Path, list[object]],
) -> None:
    """Only receipts with the given query_text_hash are returned."""
    from samantha_server.engine.decision import QueryTrace
    from samantha_server.receipts.audit import fetch_by_query_text_hash
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = query_db
    conn = _open_audit_conn(db_path)
    results = fetch_by_query_text_hash(conn, "b" * 64)
    conn.close()

    assert len(results) == 1
    trace = results[0].decision.decision_traces[0]
    assert isinstance(trace, QueryTrace)
    assert trace.query_text_hash == "b" * 64


def test_fetch_by_query_text_hash_excludes_non_query_receipts(
    query_db: tuple[Path, list[object]],
) -> None:
    """Non-query receipts are never included regardless of event_input_hash."""
    from samantha_server.receipts.audit import fetch_by_query_text_hash
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = query_db
    conn = _open_audit_conn(db_path)
    # Search with a hash that doesn't match any query trace
    results = fetch_by_query_text_hash(conn, "z" * 64)
    conn.close()

    assert results == []


def test_fetch_by_query_text_hash_returns_empty_for_unknown_hash(
    query_db: tuple[Path, list[object]],
) -> None:
    """fetch_by_query_text_hash returns empty list when no match exists."""
    from samantha_server.receipts.audit import fetch_by_query_text_hash
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = query_db
    conn = _open_audit_conn(db_path)
    results = fetch_by_query_text_hash(conn, "9" * 64)
    conn.close()

    assert results == []


def test_fetch_by_query_text_hash_returns_signed_receipt_objects(
    query_db: tuple[Path, list[object]],
) -> None:
    """All returned objects are SignedReceipt instances."""
    from samantha_server.receipts.audit import fetch_by_query_text_hash
    from samantha_server.receipts.signing import SignedReceipt
    from samantha_server.receipts.store import _open_audit_conn

    db_path, _ = query_db
    conn = _open_audit_conn(db_path)
    results = fetch_by_query_text_hash(conn, "a" * 64)
    conn.close()

    for r in results:
        assert isinstance(r, SignedReceipt)


def test_fetch_by_query_text_hash_skips_malformed_row_returns_valid(
    tmp_path: Path,
) -> None:
    """M-03: malformed row before a valid one must be skipped, not abort the scan.

    fetch_by_query_text_hash must wrap _row_to_receipt in try/except
    ReceiptPersistenceError and continue scanning so a valid match later
    in the scan is still returned.
    """
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision, QueryTrace
    from samantha_server.receipts.audit import fetch_by_query_text_hash
    from samantha_server.receipts.signing import sign_decision
    from samantha_server.receipts.store import (
        _open_audit_conn,
        _open_write_conn,
        init_store,
        insert,
    )

    db_path = tmp_path / "malformed_row.db"
    init_store(db_path)

    target_hash = "c" * 64

    # Valid receipt that should be returned
    valid_decision = EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="v" * 64,
        primitive_traces={},
        latency_us=0,
        decision_traces=(
            QueryTrace(
                query_text_hash=target_hash,
                skill_doc_hash="b" * 64,
                scenarios_cited=(),
                model_id="test-model",
                response_text_hash="d" * 64,
            ),
        ),
    )
    key = bytes(nacl.signing.SigningKey.generate())
    valid_receipt = sign_decision(valid_decision, key_id="v1", signing_key=key)

    conn = _open_write_conn(db_path)
    # Insert a malformed row directly (payload_json is invalid JSON)
    conn.execute(
        """
        INSERT INTO receipts (
            receipt_id, event_input_hash, applied_rule_id,
            next_state, outcome, signer_key_id, signature,
            payload_json, signed_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "01ARZ3NDEKTSV4RRFFQ69G5FA0",  # valid ULID format
            "m" * 64,
            None,
            "ACCESSIONING",
            "query_response",
            "v1",
            b"\x00" * 64,
            "NOT VALID JSON {{{",  # malformed payload_json
            "2025-01-01T00:00:00+00:00",
        ),
    )
    # Insert the valid receipt after the malformed one
    insert(conn, valid_receipt)
    conn.commit()
    conn.close()

    read_conn = _open_audit_conn(db_path)
    results = fetch_by_query_text_hash(read_conn, target_hash)
    read_conn.close()

    assert len(results) == 1, (
        f"Expected 1 result (valid receipt after malformed row); got {len(results)}"
    )
    assert results[0].decision.outcome == "query_response"


def test_fetch_by_query_text_hash_skips_receipts_with_empty_traces(
    tmp_path: Path,
) -> None:
    """H-09: receipt with outcome='query_response' but decision_traces==() returns nothing.

    This exercises the untested branch: the traces guard filters out receipts
    that have the right outcome but no QueryTrace to match against.
    """
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.audit import fetch_by_query_text_hash
    from samantha_server.receipts.signing import sign_decision
    from samantha_server.receipts.store import (
        _open_audit_conn,
        _open_write_conn,
        init_store,
        insert,
    )

    db_path = tmp_path / "empty_traces.db"
    init_store(db_path)

    # A receipt with outcome='query_response' but no decision_traces
    decision = EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="a" * 64,
        primitive_traces={},
        latency_us=0,
        decision_traces=(),  # empty — no QueryTrace
    )
    key = bytes(nacl.signing.SigningKey.generate())
    receipt = sign_decision(decision, key_id="v1", signing_key=key)

    conn = _open_write_conn(db_path)
    insert(conn, receipt)
    conn.commit()
    conn.close()

    read_conn = _open_audit_conn(db_path)
    results = fetch_by_query_text_hash(read_conn, "a" * 64)
    read_conn.close()

    assert results == [], (
        "fetch_by_query_text_hash must return empty list when query_response "
        "receipts have no QueryTrace in decision_traces"
    )


# ---------------------------------------------------------------------------
# QueryTrace.user_role — construction and serialization
# ---------------------------------------------------------------------------


def test_query_trace_accepts_each_valid_user_role() -> None:
    """QueryTrace can be constructed with each valid UserRole value."""
    from samantha_server.engine.decision import QueryTrace
    from samantha_server.models.roles import VALID_USER_ROLES

    for role in VALID_USER_ROLES:
        trace = QueryTrace(
            query_text_hash="a" * 64,
            skill_doc_hash="b" * 64,
            scenarios_cited=(),
            model_id="test-model",
            response_text_hash="c" * 64,
            user_role=role,  # type: ignore[arg-type]
        )
        assert trace.user_role == role


def test_query_trace_user_role_defaults_to_none() -> None:
    """QueryTrace.user_role defaults to None (backward compat with legacy receipts)."""
    from samantha_server.engine.decision import QueryTrace

    trace = QueryTrace(
        query_text_hash="a" * 64,
        skill_doc_hash="b" * 64,
        scenarios_cited=(),
        model_id="test-model",
        response_text_hash="c" * 64,
    )
    assert trace.user_role is None


def test_query_trace_user_role_serializes_and_deserializes(tmp_path: Path) -> None:
    """QueryTrace with user_role round-trips through sign→store→fetch."""
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision, QueryTrace
    from samantha_server.receipts.audit import fetch_by_event_hash
    from samantha_server.receipts.signing import sign_decision
    from samantha_server.receipts.store import (
        _open_audit_conn,
        _open_write_conn,
        init_store,
        insert,
    )

    db_path = tmp_path / "user_role_rt.db"
    init_store(db_path)

    event_hash = "f" * 64
    decision = EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash=event_hash,
        primitive_traces={},
        latency_us=0,
        decision_traces=(
            QueryTrace(
                query_text_hash="a" * 64,
                skill_doc_hash="b" * 64,
                scenarios_cited=(),
                model_id="test-model",
                response_text_hash="c" * 64,
                user_role="pathologist",
            ),
        ),
    )
    key = bytes(nacl.signing.SigningKey.generate())
    receipt = sign_decision(decision, key_id="v1", signing_key=key)

    conn = _open_write_conn(db_path)
    insert(conn, receipt)
    conn.commit()
    conn.close()

    read_conn = _open_audit_conn(db_path)
    results = fetch_by_event_hash(read_conn, event_hash)
    read_conn.close()

    assert len(results) == 1
    trace = results[0].decision.decision_traces[0]
    assert isinstance(trace, QueryTrace)
    assert trace.user_role == "pathologist"


def test_query_trace_user_role_none_round_trips(tmp_path: Path) -> None:
    """QueryTrace with user_role=None round-trips correctly (absence preserved)."""
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision, QueryTrace
    from samantha_server.receipts.audit import fetch_by_event_hash
    from samantha_server.receipts.signing import sign_decision
    from samantha_server.receipts.store import (
        _open_audit_conn,
        _open_write_conn,
        init_store,
        insert,
    )

    db_path = tmp_path / "user_role_none_rt.db"
    init_store(db_path)

    event_hash = "e" * 64
    decision = EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash=event_hash,
        primitive_traces={},
        latency_us=0,
        decision_traces=(
            QueryTrace(
                query_text_hash="a" * 64,
                skill_doc_hash="b" * 64,
                scenarios_cited=(),
                model_id="test-model",
                response_text_hash="c" * 64,
                # user_role absent — tests backward compat
            ),
        ),
    )
    key = bytes(nacl.signing.SigningKey.generate())
    receipt = sign_decision(decision, key_id="v1", signing_key=key)

    conn = _open_write_conn(db_path)
    insert(conn, receipt)
    conn.commit()
    conn.close()

    read_conn = _open_audit_conn(db_path)
    results = fetch_by_event_hash(read_conn, event_hash)
    read_conn.close()

    assert len(results) == 1
    trace = results[0].decision.decision_traces[0]
    assert isinstance(trace, QueryTrace)
    assert trace.user_role is None
