"""Integration tests for RBAC wiring on /events and /version endpoints (slice 4/7).

Verifies:
- POST /events requires events:submit capability.
- GET /version requires health:read capability.
- GET /healthz and GET /readyz are open (no RBAC).
- Proper 401/403 shapes on each protected endpoint.
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile
import time
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from samantha_server.api.event_context import EventDispatchContext
from samantha_server.engine.decision import EngineDecision
from samantha_server.llm.client import LLMResponse
from samantha_server.queue.priority import EventPriority
from samantha_server.receipts.signing import SignedReceipt

# ---------------------------------------------------------------------------
# Test key helpers
# ---------------------------------------------------------------------------

_TEST_KEY = bytes.fromhex("cafebabe" + "deadbeef" * 6 + "cafebabe")  # 32 bytes


def _make_token(capability: str, offset: int = 3600) -> str:
    """Build a valid token for *capability* using _TEST_KEY."""
    from samantha_server.api.rbac import _sign_token

    now = int(time.time())
    return _sign_token(capability, now, now + offset, hmac_key=_TEST_KEY)


def _make_expired_token(capability: str) -> str:
    """Build an already-expired token for *capability*."""
    from samantha_server.api.rbac import _sign_token

    past = int(time.time()) - 7200
    return _sign_token(capability, past, past + 3600, hmac_key=_TEST_KEY)


# ---------------------------------------------------------------------------
# App factory helpers
# ---------------------------------------------------------------------------


def _make_test_app_with_rbac() -> tuple[FastAPI, Any]:
    """Build a minimal FastAPI app with RBAC wiring (events + health routes)."""
    import sqlite3

    import samantha_server.config as cfg
    from samantha_server.api.app import RequestIDMiddleware, _BodyCapMiddleware
    from samantha_server.api.events import register_events_routes
    from samantha_server.api.health import register_health_routes
    from samantha_server.api.lifespan import AppState
    from samantha_server.api.rbac import register_rbac_exception_handlers
    from samantha_server.api.receipt_writer import ReceiptWriter
    from samantha_server.observability.cached_probe import make_langfuse_stub_probe
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.queue.priority import PriorityEventQueue
    from samantha_server.receipts.store import _SCHEMA_SQL
    from samantha_server.rules.loader import RuleIndex
    from samantha_server.skills.loader import discover

    tmp = pathlib.Path(tempfile.mkdtemp())
    rw = ReceiptWriter.open(tmp / "r.db")

    audit_conn = sqlite3.connect(":memory:", check_same_thread=False)
    audit_conn.executescript(_SCHEMA_SQL)
    audit_conn.commit()
    audit_conn.execute("PRAGMA query_only=1")

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    mock_llm.complete.return_value = LLMResponse(
        text="Test response.",
        input_tokens=10,
        output_tokens=5,
        model_id="test-model",
        latency_us=1000,
    )

    state = AppState(
        rule_index=RuleIndex([]),
        scenario_index={},
        skill_index=discover(),
        llm_client=mock_llm,
        receipt_writer=rw,
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
    register_health_routes(app)
    register_events_routes(app)
    app.add_middleware(_BodyCapMiddleware, max_request_body_bytes=cfg.MAX_REQUEST_BODY_BYTES)
    app.add_middleware(RequestIDMiddleware)
    app.state.engine = state

    return app, state


def _make_event_body() -> dict[str, Any]:
    return {
        "ctx": {
            "order": {
                "order_id": "RBAC-001",
                "patient_name": None,
                "patient_sex": "F",
                "age": 40,
                "specimen_type": "biopsy",
                "anatomic_site": "breast",
                "fixative": "formalin",
                "fixation_time_hours": 24.0,
                "ordered_tests": ["ER"],
                "priority": "routine",
                "billing_info_present": True,
            },
            "current_state": "ACCESSIONING",
            "flags": [],
            "event": {
                "event_type": "clinical_query",
                "event_data": {"query": "test"},
                "step_index": 0,
            },
        },
        "session_id": "rbac-test-session",
        "priority": "ROUTINE",
    }


# ---------------------------------------------------------------------------
# Fake decision / receipt / context helpers (copied from test_events_endpoint)
# ---------------------------------------------------------------------------


def _make_fake_decision(session_id: str = "rbac-test-session") -> EngineDecision:
    return EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="a" * 64,
        primitive_traces={},
        latency_us=100,
        session_id=session_id,
    )


def _make_fake_receipt(decision: EngineDecision) -> SignedReceipt:
    import samantha_server.config as cfg
    from samantha_server.receipts.signing import sign_decision

    return sign_decision(
        decision,
        key_id=cfg.RECEIPT_SIGNING_KEY_ID,
        signing_key=cfg.RECEIPT_SIGNING_KEY,
    )


def _make_fake_dispatch_ctx(session_id: str = "rbac-test-session") -> EventDispatchContext:
    return EventDispatchContext(
        session_id=session_id,
        priority=EventPriority.ROUTINE,
        routing_path="llm",
        queue_wait_us=50,
    )


async def _auto_enqueue(queue: Any, *, item: Any, **kwargs: Any) -> None:
    decision = _make_fake_decision(item.session_id)
    receipt = _make_fake_receipt(decision)
    ctx = _make_fake_dispatch_ctx(item.session_id)
    if not item.future.done():
        item.future.set_result((receipt, decision, ctx))


# ---------------------------------------------------------------------------
# POST /events — RBAC tests
# ---------------------------------------------------------------------------


def test_post_events_without_token_returns_401() -> None:
    """POST /events without Authorization header → 401."""
    app, state = _make_test_app_with_rbac()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=_make_event_body())

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_post_events_wrong_capability_returns_403() -> None:
    """POST /events with health:read token → 403."""
    app, state = _make_test_app_with_rbac()
    token = _make_token("health:read")

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/events",
            json=_make_event_body(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    assert resp.json() == {"error": "forbidden"}


def test_post_events_expired_token_returns_401() -> None:
    """POST /events with expired events:submit token → 401."""
    app, state = _make_test_app_with_rbac()
    token = _make_expired_token("events:submit")

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/events",
            json=_make_event_body(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_post_events_valid_token_returns_200() -> None:
    """POST /events with valid events:submit token → 200."""
    app, state = _make_test_app_with_rbac()
    token = _make_token("events:submit")

    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_auto_enqueue),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/events",
            json=_make_event_body(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert "receipt_id" in resp.json()


# ---------------------------------------------------------------------------
# GET /version — RBAC tests
# ---------------------------------------------------------------------------


def test_get_version_without_token_returns_401() -> None:
    """GET /version without Authorization header → 401."""
    app, state = _make_test_app_with_rbac()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/version")

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


def test_get_version_wrong_capability_returns_403() -> None:
    """GET /version with events:submit token → 403."""
    app, state = _make_test_app_with_rbac()
    token = _make_token("events:submit")

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/version", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403
    assert resp.json() == {"error": "forbidden"}


def test_get_version_valid_token_returns_200() -> None:
    """GET /version with valid health:read token → 200."""
    app, state = _make_test_app_with_rbac()
    token = _make_token("health:read")

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/version", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    data = resp.json()
    assert "commit" in data
    assert "config" in data


def test_get_version_expired_token_returns_401() -> None:
    """GET /version with expired health:read token → 401."""
    app, state = _make_test_app_with_rbac()
    token = _make_expired_token("health:read")

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/version", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized"}


# ---------------------------------------------------------------------------
# GET /healthz and GET /readyz — open (no RBAC)
# ---------------------------------------------------------------------------


def test_healthz_no_token_returns_200() -> None:
    """GET /healthz without any token → 200 (no RBAC on liveness probe)."""
    app, state = _make_test_app_with_rbac()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/healthz")

    assert resp.status_code == 200


def test_readyz_no_token_returns_200() -> None:
    """GET /readyz without any token → 200 (no RBAC on readiness probe)."""
    app, state = _make_test_app_with_rbac()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/readyz")

    # readyz can return 200 or 503 depending on state; RBAC must not gate it
    assert resp.status_code in (200, 503)


def test_healthz_with_token_still_200() -> None:
    """GET /healthz with a token (even expired/invalid) → 200 (probe ignores RBAC)."""
    app, state = _make_test_app_with_rbac()
    bad_token = "completely.invalid"

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/healthz", headers={"Authorization": f"Bearer {bad_token}"})

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Capability-list non-disclosure on POST /events
# ---------------------------------------------------------------------------

_CAPABILITY_STRINGS = [
    "events:submit",
    "receipts:read",
    "health:read",
    "health",
    "events",
    "receipts",
]


def test_events_401_body_does_not_enumerate_capabilities() -> None:
    """401 from POST /events must not enumerate valid capabilities."""
    app, state = _make_test_app_with_rbac()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=_make_event_body())

    assert resp.status_code == 401
    body_text = resp.text
    for cap in _CAPABILITY_STRINGS:
        assert cap not in body_text, f"Capability string {cap!r} leaked in 401 body"


def test_events_403_body_does_not_enumerate_capabilities() -> None:
    """403 from POST /events must not enumerate valid capabilities."""
    app, state = _make_test_app_with_rbac()
    token = _make_token("health:read")

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/events",
            json=_make_event_body(),
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 403
    body_text = resp.text
    for cap in _CAPABILITY_STRINGS:
        assert cap not in body_text, f"Capability string {cap!r} leaked in 403 body"
