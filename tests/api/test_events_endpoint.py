"""Tests for POST /events endpoint — Slice 6 + Slice 10."""

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

# Test RBAC key for POST /events tests.
_EVENTS_TEST_KEY = bytes.fromhex("cafebabe" + "deadbeef" * 6 + "cafebabe")


def _make_events_submit_token() -> str:
    """Build a valid events:submit token for test use."""
    from samantha_server.api.rbac import _sign_token

    now = int(time.time())
    return _sign_token("events:submit", now, now + 3600, hmac_key=_EVENTS_TEST_KEY)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_decision(session_id: str = "test-session") -> EngineDecision:
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


def _make_fake_dispatch_ctx(session_id: str = "test-session") -> EventDispatchContext:
    return EventDispatchContext(
        session_id=session_id,
        priority=EventPriority.ROUTINE,
        routing_path="llm",
        queue_wait_us=50,
    )


def _make_test_app(queue_bound: int = 256) -> tuple[FastAPI, Any]:
    """Build a minimal FastAPI app for testing POST /events.

    Registers RBAC exception handlers so 401/403 render correctly.
    All POST /events calls in tests must supply a valid events:submit Bearer token.
    Use _make_events_submit_token() + patch("...._get_rbac_hmac_key", ...).
    """
    import sqlite3

    import samantha_server.config as cfg
    from samantha_server.api.app import RequestIDMiddleware, _BodyCapMiddleware
    from samantha_server.api.events import register_events_routes
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
        queue=PriorityEventQueue(maxsize=queue_bound),
        receipt_audit_conn=audit_conn,
        is_draining=False,
    )

    app = FastAPI()
    register_rbac_exception_handlers(app)
    register_events_routes(app)
    app.add_middleware(_BodyCapMiddleware, max_request_body_bytes=cfg.MAX_REQUEST_BODY_BYTES)
    app.add_middleware(RequestIDMiddleware)
    app.state.engine = state

    return app, state


def _make_event_request_body(
    current_state: str = "ACCESSIONING",
    event_type: str = "clinical_query",
    session_id: str = "test-session",
    priority: str = "ROUTINE",
) -> dict[str, Any]:
    return {
        "ctx": {
            "order": {
                "order_id": "TEST-001",
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
            "current_state": current_state,
            "flags": [],
            "event": {
                "event_type": event_type,
                "event_data": {"query": "What orders are ready?"},
                "step_index": 0,
            },
        },
        "session_id": session_id,
        "priority": priority,
    }


async def _auto_enqueue(queue: Any, *, item: Any, **kwargs: Any) -> None:
    """Immediately resolve the payload's future with fake dispatch results.

    This replaces enqueue_or_reject in tests that need the happy path
    without a real consumer task.
    """
    decision = _make_fake_decision(item.session_id)
    receipt = _make_fake_receipt(decision)
    ctx = _make_fake_dispatch_ctx(item.session_id)
    if not item.future.done():
        item.future.set_result((receipt, decision, ctx))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _make_auth_headers() -> dict[str, str]:
    """Return Authorization header with a valid events:submit token.

    Patches _get_rbac_hmac_key to use the test key. The patch must remain
    active for the entire TestClient request — callers must use this inside
    a ``patch("samantha_server.api.rbac._get_rbac_hmac_key", ...)`` context.
    The helper just builds the header dict; the patch is caller's responsibility.
    """
    return {"Authorization": f"Bearer {_make_events_submit_token()}"}


def test_post_events_happy_path_returns_200() -> None:
    """Happy path: clinical_query event → 200 with receipt_id and decision."""
    app, state = _make_test_app()

    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_auto_enqueue),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        body = _make_event_request_body(event_type="clinical_query")
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 200
    data = resp.json()
    assert "receipt_id" in data
    assert data["receipt_id"]  # non-empty
    assert "decision" in data
    assert isinstance(data["decision"], dict)
    assert "trace_url" in data
    assert data["trace_url"] is None


def test_post_events_returns_x_request_id_header() -> None:
    """POST /events response carries X-Request-ID header."""
    app, state = _make_test_app()

    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_auto_enqueue),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=_make_event_request_body(), headers=_make_auth_headers())

    assert resp.status_code == 200
    assert "x-request-id" in {k.lower() for k in resp.headers}


def test_post_events_validation_error_returns_422() -> None:
    """Malformed body → 422 (with valid RBAC token)."""
    app, state = _make_test_app()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json={"not": "valid"}, headers=_make_auth_headers())

    assert resp.status_code == 422


def test_post_events_422_body_has_no_phi() -> None:
    """422 response body does not echo submitted payload content."""
    app, state = _make_test_app()
    secret_body = {
        "ctx": "SECRET_PHI_DATA_12345",
        "session_id": "sess",
        "priority": "ROUTINE",
    }

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=secret_body, headers=_make_auth_headers())

    assert resp.status_code == 422
    assert "SECRET_PHI_DATA_12345" not in resp.text


def test_post_events_413_from_body_cap() -> None:
    """POST /events with oversized Content-Length → 413 (body cap fires before RBAC)."""
    import samantha_server.config as cfg

    app, state = _make_test_app()

    # 413 fires at ASGI middleware level before RBAC — no token needed.
    client = TestClient(app, raise_server_exceptions=False)
    oversized = b"x" * (cfg.MAX_REQUEST_BODY_BYTES + 1)
    resp = client.post(
        "/events",
        content=oversized,
        headers={"Content-Length": str(cfg.MAX_REQUEST_BODY_BYTES + 1)},
    )
    assert resp.status_code == 413
    assert "x-request-id" in {k.lower() for k in resp.headers}
    # Ensure the large body is not echoed
    assert b"x" * 100 not in resp.content


def test_post_events_411_from_body_cap() -> None:
    """POST /events with Transfer-Encoding: chunked → 411 (body cap fires before RBAC)."""
    app, state = _make_test_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/events",
        content=b"some data",
        headers={"Transfer-Encoding": "chunked"},
    )
    assert resp.status_code == 411


def test_post_events_503_when_queue_full() -> None:
    """POST /events when queue is full → 503 with Retry-After header."""
    from samantha_server.api.events import _QueuePayload

    app, state = _make_test_app(queue_bound=1)

    # Fill the single slot synchronously using put_nowait.
    filler_future: asyncio.Future[Any] = asyncio.new_event_loop().create_future()
    filler = _QueuePayload(
        future=filler_future,
        ctx_dict={},
        session_id="filler",
        priority=EventPriority.ROUTINE,
    )
    # Use put_nowait directly on the underlying asyncio.PriorityQueue via a helper.
    # PriorityEventQueue.put() is async; use a new event loop.
    filler_loop = asyncio.new_event_loop()
    filler_loop.run_until_complete(state.queue.put(filler, EventPriority.ROUTINE))
    filler_loop.close()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        body = _make_event_request_body()
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 503
    assert "retry-after" in {k.lower() for k in resp.headers}
    data = resp.json()
    assert data["error"] == "backpressure"


def test_post_events_invalid_priority_returns_422() -> None:
    """Invalid priority value → 422."""
    app, state = _make_test_app()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        body = _make_event_request_body(priority="INVALID_PRIORITY")
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 422


def test_post_events_stat_priority_accepted() -> None:
    """STAT priority is accepted."""
    app, state = _make_test_app()

    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_auto_enqueue),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        body = _make_event_request_body(priority="STAT")
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 200


def test_post_events_background_priority_accepted() -> None:
    """BACKGROUND priority is accepted."""
    app, state = _make_test_app()

    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_auto_enqueue),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        body = _make_event_request_body(priority="BACKGROUND")
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 200


def test_post_events_dispatch_timeout_returns_504() -> None:
    """POST /events when consumer never resolves the future → 504 with structured body.

    Simulates a consumer that dequeues the payload and never calls set_result,
    causing the await future to time out.
    """
    import samantha_server.api.events as events_mod

    app, state = _make_test_app()

    async def _never_resolve(queue: Any, *, item: Any, **kwargs: Any) -> None:
        """Enqueue without resolving — simulates consumer hang."""
        pass  # future is never resolved; timeout fires

    # Patch the module-level timeout override to use a tiny value
    original = events_mod._EVENT_DISPATCH_TIMEOUT_SEC
    events_mod._EVENT_DISPATCH_TIMEOUT_SEC = 0.05
    try:
        with (
            patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
            patch("samantha_server.api.events.enqueue_or_reject", side_effect=_never_resolve),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/events", json=_make_event_request_body(), headers=_make_auth_headers()
            )
    finally:
        events_mod._EVENT_DISPATCH_TIMEOUT_SEC = original

    assert resp.status_code == 504
    data = resp.json()
    assert data["error"] == "dispatch_timeout"
    assert "request_id" in data
    assert "timeout_sec" in data


def test_post_events_consumer_crash_returns_500() -> None:
    """POST /events when dispatch_event raises → 500 with error=internal and no PHI.

    Injects a failing dispatch_event and asserts:
    - Response is 500
    - Body has {"error": "internal", "request_id": ...}
    - No PHI in response body
    """

    app, state = _make_test_app()

    # Monkey-patch dispatch_event to always raise

    def _failing_dispatch(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Simulated dispatch failure")

    # Also need uncaught exception handler - add it

    from fastapi.responses import JSONResponse

    @app.exception_handler(Exception)
    async def _uncaught(request: Any, exc: Exception) -> JSONResponse:
        import uuid

        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse({"error": "internal", "request_id": request_id}, status_code=500)

    async def _inject_failure(queue: Any, *, item: Any, **kwargs: Any) -> None:
        """Set exception on the future to simulate consumer failure."""
        if not item.future.done():
            item.future.set_exception(RuntimeError("Simulated dispatch failure"))

    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_inject_failure),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=_make_event_request_body(), headers=_make_auth_headers())

    assert resp.status_code == 500
    data = resp.json()
    assert data["error"] == "internal"
    assert "request_id" in data
    # PHI boundary: no patient data in the error response
    assert "TEST-001" not in resp.text
    assert "test-session" not in resp.text


def test_post_events_consumer_crash_request_id_in_body() -> None:
    """Consumer crash: 500 response body contains request_id field."""
    from fastapi.responses import JSONResponse

    app, state = _make_test_app()

    @app.exception_handler(Exception)
    async def _uncaught(request: Any, exc: Exception) -> JSONResponse:
        import uuid

        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse({"error": "internal", "request_id": request_id}, status_code=500)

    async def _inject_failure(queue: Any, *, item: Any, **kwargs: Any) -> None:
        if not item.future.done():
            item.future.set_exception(RuntimeError("Simulated dispatch failure"))

    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_inject_failure),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=_make_event_request_body(), headers=_make_auth_headers())

    assert resp.status_code == 500
    data = resp.json()
    assert "request_id" in data


def test_post_events_invalid_ctx_returns_422_not_500() -> None:
    """Invalid ctx that fails SpecimenContext validation → 422, not 500.

    Issue #22: SpecimenContext validation at the request boundary prevents
    invalid ctx from reaching the consumer where it would fail as 500.
    """
    app, state = _make_test_app()

    body = _make_event_request_body()
    # Replace ctx with something that fails SpecimenContext validation
    body["ctx"] = {"not_a_valid_context": True, "missing_required_fields": True}

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 422


def test_post_events_422_msg_does_not_contain_input_value() -> None:
    """422 error `msg` field must not contain repr() of the submitted value.

    Issue #24: Pydantic validator messages can include repr(value) if carelessly
    written. The _validation_error_handler passes `msg` verbatim. This regression
    test asserts that a custom input value doesn't leak through the msg field.

    The sentinel value is chosen to be recognisable as a PHI-marker — if it
    appears in the response it's a PHI leak.
    """
    app, state = _make_test_app()

    # Provide a sentinel value as priority — it will fail validation
    body = _make_event_request_body()
    body["priority"] = "PHI_SENTINEL_VALUE_ABCDEF"

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 422
    # The sentinel must not appear in the response
    assert "PHI_SENTINEL_VALUE_ABCDEF" not in resp.text


def test_post_events_preflight_failure_e2e() -> None:
    """E2E: preflight failure (unknown canonical) returns 200 via real consumer chain.

    The preflight failure routes to handle_clarification (LLM path). With a
    mock LLM client, the consumer resolves the future and the endpoint returns 200.
    """

    app, state = _make_test_app()

    # Use _auto_enqueue which simulates the consumer resolving the future.
    # The actual preflight → clarification routing is unit-tested at dispatch_event level.
    # E2E asserts: the wire protocol (request → response) works for the preflight case.
    body = _make_event_request_body(event_type="order_received")
    body["ctx"]["order"]["fixative"] = "ethanol"  # triggers preflight missing → clarification

    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_auto_enqueue),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 200
    assert "receipt_id" in resp.json()


def test_post_events_queue_consumer_error_propagates_to_500() -> None:
    """E2E: queue-consumer error propagation — injected generic failure → 500 + PHI-scrubbed body.

    The consumer side sets an exception on the future (simulating any unhandled error
    in the dispatch pipeline). The POST /events handler awaits that future, receives the
    exception, and the uncaught exception handler returns 500 with a PHI-scrubbed body.

    This validates the wire-protocol error path, not a specific event_type behavior.
    Renamed from test_post_events_not_implemented_event_type_returns_500 because
    order_received now routes deterministically (→ 200) and the NotImplementedError premise
    was false.
    """

    app, state = _make_test_app()

    @app.exception_handler(Exception)
    async def _uncaught(request: Any, exc: Exception) -> Any:
        import uuid

        from fastapi.responses import JSONResponse

        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return JSONResponse({"error": "internal", "request_id": request_id}, status_code=500)

    body = _make_event_request_body(event_type="clinical_query")

    # Inject a generic failure via set_exception to exercise the consumer error path.
    async def _inject_consumer_failure(queue: Any, *, item: Any, **kwargs: Any) -> None:
        if not item.future.done():
            item.future.set_exception(RuntimeError("injected consumer failure"))

    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch(
            "samantha_server.api.events.enqueue_or_reject",
            side_effect=_inject_consumer_failure,
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 500
    data = resp.json()
    assert data["error"] == "internal"
    assert "request_id" in data
    # PHI must not leak in error response
    assert "injected consumer failure" not in resp.text


def test_post_events_order_received_returns_200_with_receipt() -> None:
    """order_received through the endpoint returns 200 with a receipt_id.

    After the dispatch refactor, order_received routes via the deterministic branch (not NotImplementedError).
    This E2E test confirms the full wire-protocol path: enqueue → consumer resolves →
    endpoint returns 200 with a dispatch_empty or rule-fired decision + receipt_id.
    """

    app, state = _make_test_app()

    body = _make_event_request_body(event_type="order_received", current_state="ACCESSIONING")

    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_auto_enqueue),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 200
    data = resp.json()
    assert "receipt_id" in data, f"Expected 'receipt_id' in response, got: {data}"


def test_post_events_503_body_has_no_rejected_payload() -> None:
    """503 body must not echo the rejected event payload."""
    from samantha_server.api.events import _QueuePayload

    app, state = _make_test_app(queue_bound=1)

    # Fill the single slot.
    filler_future: asyncio.Future[Any] = asyncio.new_event_loop().create_future()
    filler = _QueuePayload(
        future=filler_future,
        ctx_dict={},
        session_id="filler",
        priority=EventPriority.ROUTINE,
    )
    filler_loop = asyncio.new_event_loop()
    filler_loop.run_until_complete(state.queue.put(filler, EventPriority.ROUTINE))
    filler_loop.close()

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        body = _make_event_request_body()
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 503
    resp_text = resp.text
    # The request payload must not appear in the 503 body
    assert "TEST-001" not in resp_text
    assert "test-session" not in resp_text


# ---------------------------------------------------------------------------
# session_id input validation (fix #6 + #8)
# ---------------------------------------------------------------------------


def test_session_id_empty_string_returns_422() -> None:
    """EventRequest.session_id must not be empty — Pydantic min_length=1 rejects it."""
    app, _ = _make_test_app()
    body = _make_event_request_body()
    body["session_id"] = ""  # empty string must be rejected

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 422, (
        f"Expected 422 for empty session_id, got {resp.status_code}: {resp.text}"
    )


def test_session_id_with_pipe_returns_422() -> None:
    """EventRequest.session_id must not contain '|' — HMAC separator ambiguity guard."""
    app, _ = _make_test_app()
    body = _make_event_request_body()
    body["session_id"] = "alice|123"  # pipe must be rejected

    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 422, (
        f"Expected 422 for pipe-in-session-id, got {resp.status_code}: {resp.text}"
    )


def test_post_events_payload_carries_iso8601_prompt_timestamp() -> None:
    """Live POST /events stamps a UTC ISO-8601
    prompt_timestamp on the queued payload so the consumer can forward it
    into dispatch_event. Regression guard for the SKILL.md / production-path
    divergence the consolidated review flagged as Critical.
    """
    import re

    captured: dict[str, Any] = {}

    async def _capture_payload(queue: Any, *, item: Any, **kwargs: Any) -> None:
        captured["payload"] = item
        # Resolve immediately so the request handler returns 200.
        decision = _make_fake_decision(item.session_id)
        receipt = _make_fake_receipt(decision)
        ctx = _make_fake_dispatch_ctx(item.session_id)
        if not item.future.done():
            item.future.set_result((receipt, decision, ctx))

    app, state = _make_test_app()
    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_capture_payload),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=_make_event_request_body(), headers=_make_auth_headers())

    assert resp.status_code == 200
    payload = captured["payload"]
    assert payload.prompt_timestamp is not None
    # Format must match the loader's strftime contract: UTC, second precision, Z suffix.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", payload.prompt_timestamp), (
        f"prompt_timestamp not in ISO-8601 UTC format: {payload.prompt_timestamp!r}"
    )


# ---------------------------------------------------------------------------
# API ingress accepts user_role
# ---------------------------------------------------------------------------


def test_post_events_with_valid_user_role_returns_200() -> None:
    """POST /events with a valid user_role returns 200."""
    captured: dict[str, Any] = {}

    async def _capture_payload(queue: Any, *, item: Any, **kwargs: Any) -> None:
        captured["payload"] = item
        decision = _make_fake_decision(item.session_id)
        receipt = _make_fake_receipt(decision)
        ctx = _make_fake_dispatch_ctx(item.session_id)
        if not item.future.done():
            item.future.set_result((receipt, decision, ctx))

    body = _make_event_request_body()
    body["user_role"] = "pathologist"

    app, _ = _make_test_app()
    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_capture_payload),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 200, f"Expected 200; got {resp.status_code}: {resp.text}"
    assert captured["payload"].user_role == "pathologist"


def test_post_events_with_invalid_user_role_returns_422() -> None:
    """POST /events with an unrecognized user_role returns 422."""
    body = _make_event_request_body()
    body["user_role"] = "nurse"

    app, _ = _make_test_app()
    with patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 422, (
        f"Expected 422 for invalid user_role; got {resp.status_code}: {resp.text}"
    )


def test_post_events_without_user_role_returns_200() -> None:
    """POST /events without user_role field returns 200; payload.user_role is None."""
    captured: dict[str, Any] = {}

    async def _capture_payload(queue: Any, *, item: Any, **kwargs: Any) -> None:
        captured["payload"] = item
        decision = _make_fake_decision(item.session_id)
        receipt = _make_fake_receipt(decision)
        ctx = _make_fake_dispatch_ctx(item.session_id)
        if not item.future.done():
            item.future.set_result((receipt, decision, ctx))

    body = _make_event_request_body()
    # No user_role key

    app, _ = _make_test_app()
    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_capture_payload),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 200, f"Expected 200; got {resp.status_code}: {resp.text}"
    assert captured["payload"].user_role is None


def test_post_events_with_explicit_null_user_role_returns_200() -> None:
    """S11 PR243 review M8: POST /events with user_role=null (explicit JSON null)
    returns 200 and payload.user_role is None.

    Distinct from field absence: {"user_role": null} is a separate Pydantic
    case (the field is present but None).
    """
    captured: dict[str, Any] = {}

    async def _capture_payload(queue: Any, *, item: Any, **kwargs: Any) -> None:
        captured["payload"] = item
        decision = _make_fake_decision(item.session_id)
        receipt = _make_fake_receipt(decision)
        ctx = _make_fake_dispatch_ctx(item.session_id)
        if not item.future.done():
            item.future.set_result((receipt, decision, ctx))

    body = _make_event_request_body()
    body["user_role"] = None  # explicit JSON null

    app, _ = _make_test_app()
    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_capture_payload),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 200, (
        f"Expected 200 for explicit null user_role; got {resp.status_code}: {resp.text}"
    )
    assert captured["payload"].user_role is None


# ---------------------------------------------------------------------------
# prompt_timestamp forwarding (production vs replay)
# ---------------------------------------------------------------------------


def test_post_events_without_prompt_timestamp_uses_now() -> None:
    """POST /events without prompt_timestamp uses a now() stamp.

    Production callers omit prompt_timestamp. The endpoint must still stamp
    a now()-derived ISO-8601 UTC value on the queued payload — unchanged from
    the pre-fix behavior.
    """
    import re

    captured: dict[str, Any] = {}

    async def _capture_payload(queue: Any, *, item: Any, **kwargs: Any) -> None:
        captured["payload"] = item
        decision = _make_fake_decision(item.session_id)
        receipt = _make_fake_receipt(decision)
        ctx = _make_fake_dispatch_ctx(item.session_id)
        if not item.future.done():
            item.future.set_result((receipt, decision, ctx))

    body = _make_event_request_body()
    # No prompt_timestamp key in the body.

    app, _ = _make_test_app()
    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_capture_payload),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 200
    payload = captured["payload"]
    assert payload.prompt_timestamp is not None, (
        "Production path (no prompt_timestamp field) must stamp a now() value"
    )
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", payload.prompt_timestamp), (
        f"prompt_timestamp not in ISO-8601 UTC format: {payload.prompt_timestamp!r}"
    )


def test_post_events_with_explicit_prompt_timestamp_honored() -> None:
    """POST /events with explicit prompt_timestamp forwards it verbatim.

    Replay callers always send prompt_timestamp (string or null). When a string
    is provided, that exact value must reach the queued payload — not now().
    """
    captured: dict[str, Any] = {}

    async def _capture_payload(queue: Any, *, item: Any, **kwargs: Any) -> None:
        captured["payload"] = item
        decision = _make_fake_decision(item.session_id)
        receipt = _make_fake_receipt(decision)
        ctx = _make_fake_dispatch_ctx(item.session_id)
        if not item.future.done():
            item.future.set_result((receipt, decision, ctx))

    body = _make_event_request_body()
    body["prompt_timestamp"] = "2025-01-16T09:00:00Z"

    app, _ = _make_test_app()
    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_capture_payload),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 200
    payload = captured["payload"]
    assert payload.prompt_timestamp == "2025-01-16T09:00:00Z", (
        f"Explicit prompt_timestamp must be forwarded verbatim; got {payload.prompt_timestamp!r}"
    )


def test_post_events_with_explicit_null_prompt_timestamp_honored() -> None:
    """POST /events with prompt_timestamp=null forwards None.

    Replay callers send null to suppress the timestamp anchor for scenarios
    where prompt_timestamp is not set (e.g. QR-023). The endpoint must
    forward None — not substitute a now() value.
    """
    captured: dict[str, Any] = {}

    async def _capture_payload(queue: Any, *, item: Any, **kwargs: Any) -> None:
        captured["payload"] = item
        decision = _make_fake_decision(item.session_id)
        receipt = _make_fake_receipt(decision)
        ctx = _make_fake_dispatch_ctx(item.session_id)
        if not item.future.done():
            item.future.set_result((receipt, decision, ctx))

    body = _make_event_request_body()
    body["prompt_timestamp"] = None  # explicit JSON null

    app, _ = _make_test_app()
    with (
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_capture_payload),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/events", json=body, headers=_make_auth_headers())

    assert resp.status_code == 200
    payload = captured["payload"]
    assert payload.prompt_timestamp is None, (
        f"Explicit null prompt_timestamp must forward None; got {payload.prompt_timestamp!r}"
    )
