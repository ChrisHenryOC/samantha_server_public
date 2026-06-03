"""Tests for GH-128 Slice 2: POST /events stamps samantha.environment="production".

The production parent span opened by events.py must carry
samantha.environment="production" so dashboard filters
``samantha.environment == "production"`` match emitted spans.
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


def _make_event_request_body(session_id: str = "prod-env-test") -> dict[str, Any]:
    return {
        "ctx": {
            "order": {
                "order_id": "PROD-ENV-001",
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
    """Add a fresh InMemorySpanExporter to the global TracerProvider for this test.

    Uses the ``add_span_processor`` approach so it works whether or not the
    session-level observability conftest has already installed a provider. The
    exporter is cleared before yield and after teardown to prevent cross-test
    leakage. The processor is wired to the existing (or a newly-created) SDK
    provider — it does NOT replace the global provider.

    This mirrors the technique used by ``configure_otel(test_exporter=...)``
    when a provider is already installed.
    """
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.observability.otel import configure_otel

    exporter = InMemorySpanExporter()
    # configure_otel's test_exporter branch adds a SimpleSpanProcessor to the
    # global provider (creating one if needed) and returns the exporter.
    configure_otel(CounterRegistry(), test_exporter=exporter)
    exporter.clear()
    yield exporter
    exporter.clear()


def test_post_events_parent_span_carries_production_environment(
    _local_exporter: InMemorySpanExporter,
) -> None:
    """POST /events parent span stamps environment='production' on the canonical surface.

    GH-183: samantha.environment is dropped (has langfuse.trace.metadata.* duplicate).
    Canonical surface is langfuse.environment and langfuse.trace.metadata.environment.

    The events.py handler calls ``trace.get_tracer("samantha_server")``,
    which routes to the global TracerProvider. Our local exporter is wired
    into that provider so we observe the span without replacing the provider.
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

    # GH-183: canonical surface is langfuse.environment and langfuse.trace.metadata.environment
    assert attrs.get("langfuse.environment") == "production", (
        f"Expected langfuse.environment='production', got {attrs.get('langfuse.environment')!r}"
    )
    assert attrs.get("langfuse.trace.metadata.environment") == "production", (
        "Expected langfuse.trace.metadata.environment='production', "
        f"got {attrs.get('langfuse.trace.metadata.environment')!r}"
    )
    # GH-183: samantha.environment must be absent
    assert "samantha.environment" not in attrs, (
        "GH-183: samantha.environment must not be emitted — use langfuse.trace.metadata.* instead"
    )
