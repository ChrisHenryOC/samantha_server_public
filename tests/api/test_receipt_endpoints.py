"""Tests for GET /receipts/{id} and GET /receipts pagination endpoints (GH-120).

Phase 3 Step 5.5.

Coverage:
- 200 path: known receipt id returns matching receipt JSON
- 404 path: unknown id with valid token → {"error": "receipt_not_found"}
- 401 path: missing token → 401 regardless of id existence (existence-oracle prevention)
- 403 path: wrong-capability token → 403
- Pagination 200: emit N+1 receipts, list with limit=N returns N + total=N+1
- Pagination offset: offset=N returns remaining 1
- Pagination empty: empty store → 200 with receipts=[] total=0
- Pagination past-end: large offset → 200 with receipts=[]
- Pagination ordering: descending by (signed_at_utc, receipt_id)
- 400 path: offset=-1, limit=0, limit=201 → 400 invalid_pagination
- 422 path: offset=abc → 422
- PHI absence: corpus receipts served through endpoint never expose PHI strings
- Existence-oracle: unauthenticated GET /receipts/{unknown_id} → 401 not 404
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# RBAC test key (matches conftest.py sentinel)
# ---------------------------------------------------------------------------

_TEST_KEY = bytes.fromhex("CAFEBABE" + "DEADBEEF" * 6 + "CAFEBABE")  # 32 bytes


def _make_receipts_read_token(*, expired: bool = False) -> str:
    """Issue a receipts:read Bearer token signed with _TEST_KEY."""
    from samantha_server.api.rbac import _sign_token

    now = int(time.time())
    if expired:
        return _sign_token("receipts:read", now - 7200, now - 3600, hmac_key=_TEST_KEY)
    return _sign_token("receipts:read", now, now + 3600, hmac_key=_TEST_KEY)


def _make_wrong_cap_token() -> str:
    """Issue an events:submit token (wrong capability for receipts routes)."""
    from samantha_server.api.rbac import _sign_token

    now = int(time.time())
    return _sign_token("events:submit", now, now + 3600, hmac_key=_TEST_KEY)


# ---------------------------------------------------------------------------
# App factory for receipt endpoint tests
# ---------------------------------------------------------------------------


def _make_test_app(
    write_conn: sqlite3.Connection,
    audit_conn: sqlite3.Connection,
) -> FastAPI:
    """Build a minimal FastAPI app with receipt routes wired."""
    import asyncio
    from unittest.mock import MagicMock

    from samantha_server.api.lifespan import AppState
    from samantha_server.api.rbac import register_rbac_exception_handlers
    from samantha_server.api.receipt_writer import ReceiptWriter
    from samantha_server.api.receipts import register_receipts_routes
    from samantha_server.observability.cached_probe import make_langfuse_stub_probe
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.queue.priority import PriorityEventQueue
    from samantha_server.rules.loader import RuleIndex

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"

    receipt_writer = ReceiptWriter(write_conn)

    state = AppState(
        rule_index=RuleIndex([]),
        scenario_index={},
        skill_index={},
        llm_client=mock_llm,
        receipt_writer=receipt_writer,
        receipt_write_lock=asyncio.Lock(),
        counters=CounterRegistry(),
        langfuse_probe=make_langfuse_stub_probe(),
        commit_sha="test-sha",
        queue=PriorityEventQueue(maxsize=256),
        receipt_audit_conn=audit_conn,
        is_draining=False,
    )

    app = FastAPI()
    register_rbac_exception_handlers(app)
    register_receipts_routes(app)
    app.state.engine = state
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conns(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Return (write_conn, audit_conn) sharing the same file-backed receipts DB."""
    from samantha_server.receipts.store import _open_audit_conn, _open_write_conn, init_store

    db_path = tmp_path / "receipts.db"
    init_store(db_path)
    write_conn = _open_write_conn(db_path)
    audit_conn = _open_audit_conn(db_path)
    yield write_conn, audit_conn
    write_conn.close()
    audit_conn.close()


def _make_signed_receipt(
    event_hash: str = "a" * 64,
    rule_id: str | None = "ACC-001",
) -> Any:
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


def _insert_receipt(write_conn: sqlite3.Connection, receipt: Any) -> None:
    from samantha_server.receipts.store import insert

    insert(write_conn, receipt)


# ---------------------------------------------------------------------------
# GET /receipts/{receipt_id} — 200 path
# ---------------------------------------------------------------------------


def test_get_receipt_by_id_returns_200_with_receipt_json(
    db_conns: tuple,
) -> None:
    """GET /receipts/{id} with valid receipts:read token and known id → 200."""
    write_conn, audit_conn = db_conns
    receipt = _make_signed_receipt()
    _insert_receipt(write_conn, receipt)

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            f"/receipts/{receipt.receipt_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["receipt_id"] == receipt.receipt_id


def test_get_receipt_by_id_response_shape(
    db_conns: tuple,
) -> None:
    """GET /receipts/{id} 200 response has the expected SignedReceipt-compatible keys."""
    write_conn, audit_conn = db_conns
    receipt = _make_signed_receipt()
    _insert_receipt(write_conn, receipt)

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            f"/receipts/{receipt.receipt_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    # Must have the Phase-2 SignedReceipt keys
    assert "receipt_id" in body
    assert "decision" in body
    assert "signer_key_id" in body
    assert "signature" in body
    assert "signed_at_utc" in body


# ---------------------------------------------------------------------------
# GET /receipts/{receipt_id} — 404 path
# ---------------------------------------------------------------------------


def test_get_receipt_by_id_unknown_id_returns_404(
    db_conns: tuple,
) -> None:
    """GET /receipts/{unknown_id} with valid token → 404 {"error": "receipt_not_found"}."""
    write_conn, audit_conn = db_conns
    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/receipts/01AAAAAAAAAAAAAAAAAAAAAAAAA",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 404
    assert resp.json() == {"error": "receipt_not_found"}


# ---------------------------------------------------------------------------
# GET /receipts/{receipt_id} — 401 path (existence-oracle prevention)
# ---------------------------------------------------------------------------


def test_get_receipt_by_id_no_token_returns_401(
    db_conns: tuple,
) -> None:
    """GET /receipts/{unknown_id} with no token → 401 (not 404)."""
    write_conn, audit_conn = db_conns
    app = _make_test_app(write_conn, audit_conn)

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/receipts/01AAAAAAAAAAAAAAAAAAAAAAAAA")

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_get_receipt_by_id_known_id_no_token_returns_401(
    db_conns: tuple,
) -> None:
    """GET /receipts/{known_id} with no token → 401 (existence-oracle prevention)."""
    write_conn, audit_conn = db_conns
    receipt = _make_signed_receipt()
    _insert_receipt(write_conn, receipt)

    app = _make_test_app(write_conn, audit_conn)

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/receipts/{receipt.receipt_id}")

    # Must be 401 even for a known id — existence oracle prevention
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


# ---------------------------------------------------------------------------
# GET /receipts/{receipt_id} — 403 path
# ---------------------------------------------------------------------------


def test_get_receipt_by_id_wrong_capability_returns_403(
    db_conns: tuple,
) -> None:
    """GET /receipts/{id} with events:submit token → 403."""
    write_conn, audit_conn = db_conns
    receipt = _make_signed_receipt()
    _insert_receipt(write_conn, receipt)

    app = _make_test_app(write_conn, audit_conn)
    token = _make_wrong_cap_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            f"/receipts/{receipt.receipt_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    assert resp.json() == {"error": "forbidden"}


# ---------------------------------------------------------------------------
# GET /receipts — pagination 200 path
# ---------------------------------------------------------------------------


def test_list_receipts_returns_200_with_envelope(
    db_conns: tuple,
) -> None:
    """GET /receipts with valid token → 200 with {receipts, offset, limit, total}."""
    write_conn, audit_conn = db_conns
    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/receipts", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert "receipts" in body
    assert "offset" in body
    assert "limit" in body
    assert "total" in body


def test_list_receipts_pagination_limit(
    db_conns: tuple,
) -> None:
    """list with limit=N returns N receipts; total=N+1."""
    write_conn, audit_conn = db_conns

    # Insert 4 receipts
    for h in "abcd":
        _insert_receipt(write_conn, _make_signed_receipt(event_hash=h * 64))

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/receipts?offset=0&limit=3",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["receipts"]) == 3
    assert body["total"] == 4
    assert body["offset"] == 0
    assert body["limit"] == 3


def test_list_receipts_pagination_offset(
    db_conns: tuple,
) -> None:
    """list with offset=N returns the remaining receipts after the first page."""
    write_conn, audit_conn = db_conns

    # Insert 4 receipts
    for h in "abcd":
        _insert_receipt(write_conn, _make_signed_receipt(event_hash=h * 64))

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/receipts?offset=3&limit=3",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["receipts"]) == 1
    assert body["total"] == 4


def test_list_receipts_empty_store_returns_200(
    db_conns: tuple,
) -> None:
    """GET /receipts on empty store → 200 with receipts=[], total=0."""
    write_conn, audit_conn = db_conns
    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/receipts", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["receipts"] == []
    assert body["total"] == 0


def test_list_receipts_past_end_returns_200_empty(
    db_conns: tuple,
) -> None:
    """GET /receipts with large offset on small store → 200 with receipts=[], total=actual."""
    write_conn, audit_conn = db_conns
    _insert_receipt(write_conn, _make_signed_receipt())

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/receipts?offset=10000&limit=50",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["receipts"] == []
    assert body["total"] == 1  # actual count, not 0


# ---------------------------------------------------------------------------
# GET /receipts — ordering
# ---------------------------------------------------------------------------


def test_list_receipts_ordering_descending(
    db_conns: tuple,
) -> None:
    """GET /receipts returns receipts in descending (signed_at_utc, receipt_id) order."""
    write_conn, audit_conn = db_conns

    # Insert 3 receipts then update signed_at_utc to force known ordering
    receipts = [_make_signed_receipt(event_hash=h * 64) for h in "abc"]
    for r in receipts:
        _insert_receipt(write_conn, r)

    # Assign explicit signed_at_utc: 'a' is oldest, 'c' is newest

    raw = sqlite3.connect(str(Path(write_conn.execute("PRAGMA database_list").fetchone()[2])))
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

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/receipts", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    result_receipts = body["receipts"]
    assert len(result_receipts) == 3
    # First result = newest (c), last = oldest (a)
    assert result_receipts[0]["decision"]["event_input_hash"] == "c" * 64
    assert result_receipts[2]["decision"]["event_input_hash"] == "a" * 64


def test_list_receipts_receipt_id_desc_tiebreak(
    db_conns: tuple,
) -> None:
    """GET /receipts uses receipt_id DESC as tie-break when signed_at_utc is equal.

    Regression for GH-120 review finding #5: the secondary sort is now
    pinned by a test. When two receipts share the same signed_at_utc, the
    lex-larger receipt_id must appear first in the response.
    """
    import sqlite3
    from pathlib import Path

    write_conn, audit_conn = db_conns

    r1 = _make_signed_receipt(event_hash="f" * 64)
    r2 = _make_signed_receipt(event_hash="g" * 64)
    _insert_receipt(write_conn, r1)
    _insert_receipt(write_conn, r2)

    # Force both to the same signed_at_utc to exercise the tie-break.
    same_ts = "2025-06-01T12:00:00+00:00"
    raw = sqlite3.connect(str(Path(write_conn.execute("PRAGMA database_list").fetchone()[2])))
    raw.execute("UPDATE receipts SET signed_at_utc = ?", (same_ts,))
    raw.commit()
    raw.close()

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/receipts?limit=2", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    result_receipts = body["receipts"]
    assert len(result_receipts) == 2

    first_id = result_receipts[0]["receipt_id"]
    second_id = result_receipts[1]["receipt_id"]
    assert first_id > second_id, f"Expected receipt_id DESC tie-break: {first_id!r} > {second_id!r}"


# ---------------------------------------------------------------------------
# GET /receipts — 400 invalid pagination
# ---------------------------------------------------------------------------


def test_list_receipts_negative_offset_returns_400(
    db_conns: tuple,
) -> None:
    """GET /receipts?offset=-1 → 400 {"error": "invalid_pagination"}."""
    write_conn, audit_conn = db_conns
    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/receipts?offset=-1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid_pagination"}


def test_list_receipts_zero_limit_returns_400(
    db_conns: tuple,
) -> None:
    """GET /receipts?limit=0 → 400 {"error": "invalid_pagination"}."""
    write_conn, audit_conn = db_conns
    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/receipts?limit=0",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid_pagination"}


def test_list_receipts_limit_over_200_returns_400(
    db_conns: tuple,
) -> None:
    """GET /receipts?limit=201 → 400 {"error": "invalid_pagination"}."""
    write_conn, audit_conn = db_conns
    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/receipts?limit=201",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid_pagination"}


# ---------------------------------------------------------------------------
# GET /receipts — 422 non-integer parameters
# ---------------------------------------------------------------------------


def test_list_receipts_limit_max_valid(
    db_conns: tuple,
) -> None:
    """GET /receipts?limit=200 → 200 (max valid limit)."""
    write_conn, audit_conn = db_conns
    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/receipts?limit=200",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200


def test_list_receipts_limit_min_valid(
    db_conns: tuple,
) -> None:
    """GET /receipts?limit=1 → 200 (min valid limit) with at most one receipt."""
    write_conn, audit_conn = db_conns
    # Insert one receipt so we can confirm it appears in the response
    _insert_receipt(write_conn, _make_signed_receipt(event_hash="h" * 64))

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/receipts?limit=1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["receipts"]) == 1


def test_list_receipts_offset_zero_explicit(
    db_conns: tuple,
) -> None:
    """GET /receipts?offset=0&limit=1 → 200 (explicit offset=0 is valid)."""
    write_conn, audit_conn = db_conns
    _insert_receipt(write_conn, _make_signed_receipt(event_hash="i" * 64))

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/receipts?offset=0&limit=1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["receipts"]) == 1
    assert body["offset"] == 0


def test_list_receipts_offset_over_max_returns_400(
    db_conns: tuple,
) -> None:
    """GET /receipts?offset=10_000_001 → 400 {"error": "invalid_pagination"}."""
    write_conn, audit_conn = db_conns
    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/receipts?offset=10000001",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid_pagination"}


def test_list_receipts_non_integer_offset_returns_422(
    db_conns: tuple,
) -> None:
    """GET /receipts?offset=abc → 422 (FastAPI/pydantic default)."""
    write_conn, audit_conn = db_conns
    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/receipts?offset=abc",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /receipts — 401/403 for list endpoint
# ---------------------------------------------------------------------------


def test_list_receipts_no_token_returns_401(
    db_conns: tuple,
) -> None:
    """GET /receipts without Authorization → 401."""
    write_conn, audit_conn = db_conns
    app = _make_test_app(write_conn, audit_conn)

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/receipts")

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_list_receipts_wrong_capability_returns_403(
    db_conns: tuple,
) -> None:
    """GET /receipts with events:submit token → 403."""
    write_conn, audit_conn = db_conns
    app = _make_test_app(write_conn, audit_conn)
    token = _make_wrong_cap_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/receipts", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403
    assert resp.json() == {"error": "forbidden"}


# ---------------------------------------------------------------------------
# PHI absence: no PHI fixture strings in any response body
# ---------------------------------------------------------------------------

# PHI test strings that must never appear verbatim in any API response.
# These represent patient data fragments that SignedReceipt should never
# surface (it only carries hashes, not raw values).
# "TEST, Alice" and "TEST, Bob" are drawn from the actual scenario corpus
# (tests/scenarios/test_replay.py) and represent the canonical test-patient
# name format used in ACC-001 / ACC-006 / IHC-001 fixtures.
_PHI_STRINGS = [
    "John Doe",
    "Jane Smith",
    "Mary Johnson",
    "patient_name",
    "social_security",
    "date_of_birth",
    "TEST, Alice",
    "TEST, Bob",
]


def test_get_receipt_response_contains_no_phi_strings(
    db_conns: tuple,
) -> None:
    """GET /receipts/{id} response body must not contain any PHI fixture strings."""
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import sign_decision

    write_conn, audit_conn = db_conns

    # Build a receipt whose EngineDecision uses PHI-string-bearing event data hashes.
    # The receipt itself stores only the hash, so PHI strings should never appear.
    decision = EngineDecision(
        applied_rule_id="ACC-001",
        next_state="HOLD",
        flags_added=(),
        flags_cleared=(),
        outcome="hold_missing_name",
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash="phi_input_hash_" + "a" * 49,  # not a real PHI string
        primitive_traces={},
        latency_us=10,
    )
    key = bytes(nacl.signing.SigningKey.generate())
    receipt = sign_decision(decision, key_id="v1", signing_key=key)
    _insert_receipt(write_conn, receipt)

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            f"/receipts/{receipt.receipt_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body_text = resp.text
    for phi in _PHI_STRINGS:
        assert phi not in body_text, f"PHI string {phi!r} found in response body"


def test_list_receipts_response_contains_no_phi_strings(
    db_conns: tuple,
) -> None:
    """GET /receipts response body must not contain any PHI fixture strings."""
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision
    from samantha_server.receipts.signing import sign_decision

    write_conn, audit_conn = db_conns

    # Insert several receipts with normal event_input_hashes
    for i in range(3):
        decision = EngineDecision(
            applied_rule_id="ACC-001",
            next_state="HOLD",
            flags_added=(),
            flags_cleared=(),
            outcome="hold_missing_name",
            also_matched=(),
            dispatched_rule_ids=("ACC-001",),
            event_input_hash=str(i).zfill(64),
            primitive_traces={},
            latency_us=10,
        )
        key = bytes(nacl.signing.SigningKey.generate())
        receipt = sign_decision(decision, key_id="v1", signing_key=key)
        _insert_receipt(write_conn, receipt)

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/receipts", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body_text = resp.text
    for phi in _PHI_STRINGS:
        assert phi not in body_text, f"PHI string {phi!r} found in response body"


# ---------------------------------------------------------------------------
# PHI regression: CanonicalizationTrace.raw_value and PrimitiveTrace.actual
# must not appear in the HTTP response.
# ---------------------------------------------------------------------------


def test_get_receipt_redacts_canonicalization_trace_raw_value(
    db_conns: tuple,
) -> None:
    """Regression for Critical PHI leak: CanonicalizationTrace.raw_value
    must not appear in the GET /receipts/{id} response body."""
    import nacl.signing

    from samantha_server.engine.decision import CanonicalizationTrace, EngineDecision
    from samantha_server.receipts.signing import sign_decision

    sentinel = "PHI_SENTINEL_RAW_VALUE_XYZZY"
    write_conn, audit_conn = db_conns

    decision = EngineDecision(
        applied_rule_id="ACC-001",
        next_state="HOLD",
        flags_added=(),
        flags_cleared=(),
        outcome="hold_missing_name",
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash="a" * 64,
        primitive_traces={},
        latency_us=10,
        decision_traces=(
            CanonicalizationTrace(
                field="specimen_type",
                raw_value=sentinel,
                canonical_value_attempted="UNKNOWN",
            ),
        ),
    )
    key = bytes(nacl.signing.SigningKey.generate())
    receipt = sign_decision(decision, key_id="v1", signing_key=key)
    _insert_receipt(write_conn, receipt)

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            f"/receipts/{receipt.receipt_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert sentinel not in resp.text, (
        f"CanonicalizationTrace.raw_value sentinel {sentinel!r} found in response body"
    )


def test_get_receipt_redacts_primitive_trace_actual(
    db_conns: tuple,
) -> None:
    """Regression: PrimitiveTrace.actual must not appear in GET /receipts/{id} response."""
    import nacl.signing

    from samantha_server.engine.decision import EngineDecision
    from samantha_server.primitives.trace import PrimitiveTrace
    from samantha_server.receipts.signing import sign_decision

    sentinel = "PHI_SENTINEL_PRIMITIVE_ACTUAL_QUUX"
    write_conn, audit_conn = db_conns

    decision = EngineDecision(
        applied_rule_id="ACC-001",
        next_state="HOLD",
        flags_added=(),
        flags_cleared=(),
        outcome="hold_missing_name",
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash="b" * 64,
        primitive_traces={
            "ACC-001": PrimitiveTrace(
                primitive="Equals",
                field="patient_name",
                expected="",
                actual=sentinel,
                result=True,
            )
        },
        latency_us=10,
    )
    key = bytes(nacl.signing.SigningKey.generate())
    receipt = sign_decision(decision, key_id="v1", signing_key=key)
    _insert_receipt(write_conn, receipt)

    app = _make_test_app(write_conn, audit_conn)
    token = _make_receipts_read_token()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            f"/receipts/{receipt.receipt_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert sentinel not in resp.text, (
        f"PrimitiveTrace.actual sentinel {sentinel!r} found in response body"
    )
