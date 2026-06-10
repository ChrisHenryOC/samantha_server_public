"""Tests for Slice 3: prod-path span uses canonical langfuse.trace.metadata.* surface.

After migration, events.py stamps the parent span via stamp_trace_attributes(TraceContext(...))
using ENVIRONMENT_PRODUCTION. The canonical dashboard surface is langfuse.trace.metadata.*;
engine-internal samantha.* keys are still emitted. The 7 dropped samantha.* keys must
be absent (regression prevention).
"""

from __future__ import annotations

import asyncio
import pathlib
import tempfile
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from samantha_server.api.event_context import EventDispatchContext
from samantha_server.engine.decision import EngineDecision
from samantha_server.llm.client import LLMResponse
from samantha_server.observability.otel import PARENT_SPAN_NAME
from samantha_server.queue.priority import EventPriority
from samantha_server.receipts.signing import SignedReceipt

_EVENTS_TEST_KEY = bytes.fromhex("cafebabe" + "deadbeef" * 6 + "cafebabe")


def _make_events_submit_token() -> str:
    from samantha_server.api.rbac import _sign_token

    now = int(time.time())
    return _sign_token("events:submit", now, now + 3600, hmac_key=_EVENTS_TEST_KEY)


def _make_fake_decision(session_id: str = "prod-trace-test") -> EngineDecision:
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


def _make_fake_dispatch_ctx(session_id: str = "prod-trace-test") -> EventDispatchContext:
    return EventDispatchContext(
        session_id=session_id,
        priority=EventPriority.ROUTINE,
        routing_path="llm",
        queue_wait_us=50,
    )


def _make_test_app() -> tuple[FastAPI, Any]:
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
        queue=PriorityEventQueue(maxsize=256),
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


def _make_event_request_body(session_id: str = "prod-trace-test") -> dict[str, Any]:
    return {
        "ctx": {
            "order": {
                "order_id": "PROD-TRACE-001",
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
                "event_data": {"query": "Is fixation time adequate?"},
                "step_index": 0,
            },
        },
        "session_id": session_id,
        "priority": "ROUTINE",
    }


@pytest.fixture
def _local_exporter() -> Iterator[InMemorySpanExporter]:
    """Add a fresh InMemorySpanExporter to the global TracerProvider for this test."""
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.observability.otel import configure_otel

    exporter = InMemorySpanExporter()
    configure_otel(CounterRegistry(), test_exporter=exporter)
    exporter.clear()
    yield exporter
    exporter.clear()


def test_prod_path_span_carries_canonical_langfuse_metadata_surface(
    _local_exporter: InMemorySpanExporter,
) -> None:
    """POST /events prod-path parent span carries langfuse.trace.metadata.* attrs.

    events.py migrates from direct set_span_attribute calls to
    stamp_trace_attributes(TraceContext(environment=ENVIRONMENT_PRODUCTION)).
    Canonical dashboard surface = langfuse.trace.metadata.*.
    """
    app, _state = _make_test_app()
    token = _make_events_submit_token()
    body = _make_event_request_body()

    decision = _make_fake_decision(body["session_id"])
    receipt = _make_fake_receipt(decision)
    dispatch_ctx = _make_fake_dispatch_ctx(body["session_id"])

    async def _fake_enqueue(queue, *, item, priority, counters, retry_after_sec, otel_context):  # type: ignore[no-untyped-def]
        item.future.set_result((receipt, decision, dispatch_ctx))

    with (
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_fake_enqueue),
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
    ):
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/events",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text

    spans = _local_exporter.get_finished_spans()
    parent_spans = [s for s in spans if s.name == PARENT_SPAN_NAME]
    assert len(parent_spans) >= 1, (
        f"Expected at least 1 {PARENT_SPAN_NAME!r} span, got {len(parent_spans)}: "
        f"{[s.name for s in spans]}"
    )
    attrs = dict(parent_spans[0].attributes or {})

    # 1. langfuse.environment and langfuse.trace.metadata.environment = "production"
    assert attrs.get("langfuse.environment") == "production", (
        f"Expected langfuse.environment='production', got {attrs.get('langfuse.environment')!r}"
    )
    assert attrs.get("langfuse.trace.metadata.environment") == "production", (
        "Expected langfuse.trace.metadata.environment='production', "
        f"got {attrs.get('langfuse.trace.metadata.environment')!r}"
    )

    # 2. Canonical metadata surface is present
    assert "langfuse.trace.metadata.routing_path" in attrs, (
        "langfuse.trace.metadata.routing_path missing from prod-path span"
    )
    assert "langfuse.trace.metadata.next_state" in attrs, (
        "langfuse.trace.metadata.next_state missing from prod-path span"
    )
    assert "langfuse.trace.metadata.outcome" in attrs, (
        "langfuse.trace.metadata.outcome missing from prod-path span"
    )

    # 3. Engine-internal samantha.* keys still present
    assert "samantha.session_id" in attrs, "samantha.session_id must be present"
    assert "samantha.priority" in attrs, "samantha.priority must be present"
    assert "samantha.event_input_hash" in attrs, "samantha.event_input_hash must be present"
    assert "samantha.latency_us" in attrs, "samantha.latency_us must be present"
    assert "samantha.receipt_id" in attrs, "samantha.receipt_id must be present"

    # 4. Dropped samantha.* keys must be absent (regression prevention).
    # The 3 keys not listed here (scenario_id, scenario_category, sweep_run_id)
    # are structurally impossible on the prod span because the prod TraceContext
    # never populates those fields — the assertion stays symmetric with the
    # 7-key set used at other call sites in case a future regression adds them.
    dropped = {
        "samantha.routing_path",
        "samantha.next_state",
        "samantha.outcome",
        "samantha.environment",
        "samantha.scenario_id",
        "samantha.scenario_category",
        "samantha.sweep_run_id",
    }
    present_dropped = dropped & set(attrs.keys())
    assert not present_dropped, (
        f"Dropped samantha.* keys must be absent from prod-path span: {present_dropped!r}"
    )


def test_prod_path_backpressure_rejection_span_carries_pre_dispatch_environment(
    _local_exporter: InMemorySpanExporter,
) -> None:
    """When enqueue_or_reject raises BackpressureRejection, the span only carries
    the pre-dispatch TraceContext (session_id + priority + environment). This test
    pins that the rejection-path span is still labelled environment=production
    and does not silently re-introduce any dropped samantha.* key.

     review (#5): the happy-path test alone wouldn't catch a regression
    that re-introduces a dropped samantha.* key in the pre-dispatch TraceContext.
    """
    from samantha_server.api.backpressure import BackpressureRejection

    app, _state = _make_test_app()
    token = _make_events_submit_token()
    body = _make_event_request_body()

    rejection = BackpressureRejection(bound=256, depth=256, retry_after_sec=1)

    async def _reject(queue, *, item, priority, counters, retry_after_sec, otel_context):  # type: ignore[no-untyped-def]
        raise rejection

    with (
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_reject),
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
    ):
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/events",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 503, resp.text

    spans = _local_exporter.get_finished_spans()
    parent_spans = [s for s in spans if s.name == PARENT_SPAN_NAME]
    assert len(parent_spans) >= 1
    attrs = dict(parent_spans[0].attributes or {})

    # Pre-dispatch environment stamping landed correctly on the rejected span.
    assert attrs.get("langfuse.environment") == "production"
    assert attrs.get("langfuse.trace.metadata.environment") == "production"

    # Engine-internal pre-dispatch fields present.
    assert "samantha.session_id" in attrs
    assert "samantha.priority" in attrs

    # All 7 dropped samantha.* keys remain absent on the rejection path too.
    dropped = {
        "samantha.routing_path",
        "samantha.next_state",
        "samantha.outcome",
        "samantha.environment",
        "samantha.scenario_id",
        "samantha.scenario_category",
        "samantha.sweep_run_id",
    }
    present_dropped = dropped & set(attrs.keys())
    assert not present_dropped, (
        f"Dropped samantha.* keys must be absent on backpressure-rejection "
        f"span too: {present_dropped!r}"
    )


def test_prod_path_span_carries_order_id_attribute(
    _local_exporter: InMemorySpanExporter,
) -> None:
    """Post-dispatch parent span carries samantha.order_id from decision.order_id."""
    app, _state = _make_test_app()
    token = _make_events_submit_token()
    body = _make_event_request_body()

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
        latency_us=100,
        session_id=body["session_id"],
        order_id="PROD-TRACE-001",
    )
    receipt = _make_fake_receipt(decision)
    dispatch_ctx = _make_fake_dispatch_ctx(body["session_id"])

    async def _fake_enqueue(queue, *, item, priority, counters, retry_after_sec, otel_context):  # type: ignore[no-untyped-def]
        item.future.set_result((receipt, decision, dispatch_ctx))

    with (
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_fake_enqueue),
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
    ):
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/events",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    spans = _local_exporter.get_finished_spans()
    parent_spans = [s for s in spans if s.name == PARENT_SPAN_NAME]
    assert len(parent_spans) >= 1
    attrs = dict(parent_spans[0].attributes or {})
    assert attrs.get("samantha.order_id") == "PROD-TRACE-001", (
        f"Expected samantha.order_id='PROD-TRACE-001', got {attrs.get('samantha.order_id')!r}"
    )


def test_prod_path_span_omits_order_id_attribute_when_none(
    _local_exporter: InMemorySpanExporter,
) -> None:
    """Post-dispatch span must NOT carry samantha.order_id when decision.order_id is None.

    OTel attributes do not accept None values; absence is the correct signal
    when no order_id was stamped on the decision. Verifies that the conditional
    in stamp_trace_attributes (ctx.order_id is not None) is respected on the
    prod path.
    """
    app, _state = _make_test_app()
    token = _make_events_submit_token()
    body = _make_event_request_body()

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
        latency_us=100,
        session_id=body["session_id"],
        order_id=None,
    )
    receipt = _make_fake_receipt(decision)
    dispatch_ctx = _make_fake_dispatch_ctx(body["session_id"])

    async def _fake_enqueue(queue, *, item, priority, counters, retry_after_sec, otel_context):  # type: ignore[no-untyped-def]
        item.future.set_result((receipt, decision, dispatch_ctx))

    with (
        patch("samantha_server.api.events.enqueue_or_reject", side_effect=_fake_enqueue),
        patch("samantha_server.api.rbac._get_rbac_hmac_key", return_value=_EVENTS_TEST_KEY),
    ):
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post(
            "/events",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    spans = _local_exporter.get_finished_spans()
    parent_spans = [s for s in spans if s.name == PARENT_SPAN_NAME]
    assert len(parent_spans) >= 1
    attrs = dict(parent_spans[0].attributes or {})
    assert "samantha.order_id" not in attrs, (
        "samantha.order_id must be absent from the prod-path span when decision.order_id is None; "
        f"got {attrs.get('samantha.order_id')!r}"
    )
