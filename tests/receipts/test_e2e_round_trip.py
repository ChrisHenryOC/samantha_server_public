"""E2E receipt round-trip — POST /events → SQLite → verify_signature.

Drives one deterministic-path event (order_received → ACC-008) through the
real production transport: POST /events → priority queue → _consume consumer →
dispatch_event → emit_receipt → SQLite. Then fetches the persisted receipt by
receipt_id and confirms verify_signature == VALID.

This is a gap test: the signing, audit, and endpoint layers had unit tests in
isolation but no end-to-end test that covered the full
  sign → persist → fetch → verify
round-trip in a single test.

Follows the established pattern from tests/api/test_replay_through_events.py
(anyio.run + httpx.AsyncClient + real consumer task + tmp_path receipts DB).
No LLM is required — ACC-008 is a purely deterministic rule.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sqlite3
import time
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
from fastapi import FastAPI

# ---------------------------------------------------------------------------
# RBAC test key (mirrors test_events_endpoint.py)
# ---------------------------------------------------------------------------

_EVENTS_TEST_KEY = bytes.fromhex("cafebabe" + "deadbeef" * 6 + "cafebabe")


def _make_auth_headers() -> dict[str, str]:
    from samantha_server.api.rbac import _sign_token

    now = int(time.time())
    token = _sign_token("events:submit", now, now + 3600, hmac_key=_EVENTS_TEST_KEY)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# SC-001 step 1 event body (ACC-008 deterministic path)
# ---------------------------------------------------------------------------

_SC001_ORDER: dict[str, Any] = {
    "order_id": "SC-001",
    "patient_name": None,
    "patient_sex": "F",
    "age": 58,
    "specimen_type": "biopsy",
    "anatomic_site": "breast",
    "fixative": "formalin",
    "fixation_time_hours": 24.0,
    "ordered_tests": ["Breast IHC Panel"],
    "priority": "routine",
    "billing_info_present": True,
}


def _make_step1_body() -> dict[str, Any]:
    return {
        "ctx": {
            "order": _SC001_ORDER,
            "current_state": "ACCESSIONING",
            "flags": [],
            "event": {
                "event_type": "order_received",
                "event_data": {
                    "patient_name": "TESTPATIENT-0001, Michael",
                    "age": 58,
                    "sex": "F",
                    "specimen_type": "biopsy",
                    "anatomic_site": "breast",
                    "fixative": "formalin",
                    "fixation_time_hours": 24.0,
                    "ordered_tests": ["Breast IHC Panel"],
                    "priority": "routine",
                    "billing_info_present": True,
                },
                "step_index": 1,
            },
        },
        "session_id": "e2e-round-trip-test",
        "priority": "ROUTINE",
    }


# ---------------------------------------------------------------------------
# App builder
# ---------------------------------------------------------------------------


def _build_app(tmp: pathlib.Path) -> tuple[FastAPI, Any]:
    import samantha_server.config as cfg
    from samantha_server.api.app import RequestIDMiddleware, _BodyCapMiddleware
    from samantha_server.api.events import register_events_routes
    from samantha_server.api.lifespan import AppState
    from samantha_server.api.rbac import register_rbac_exception_handlers
    from samantha_server.api.receipt_writer import ReceiptWriter
    from samantha_server.llm.client import LLMResponse
    from samantha_server.observability.cached_probe import make_langfuse_stub_probe
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.queue.priority import PriorityEventQueue
    from samantha_server.receipts.store import _SCHEMA_SQL
    from samantha_server.rules.loader import RuleIndex, load_rule_specs
    from samantha_server.skills.loader import discover

    specs_dir = pathlib.Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"
    rule_index = RuleIndex(load_rule_specs(specs_dir))

    mock_llm = MagicMock()
    mock_llm.model_id = "test-model"
    mock_llm.complete.return_value = LLMResponse(
        text="Test response.",
        input_tokens=10,
        output_tokens=5,
        model_id="test-model",
        latency_us=1000,
    )

    receipt_writer = ReceiptWriter.open(tmp / "receipts.db")

    # Lifecycle note: this in-memory audit conn and the real
    # tmp_path ReceiptWriter are both owned by AppState — aclose() in the
    # finally below closes them (writer close never raises by contract).
    # The separate read connection later in the test is finally-closed inline.
    audit_conn = sqlite3.connect(":memory:", check_same_thread=False)
    audit_conn.executescript(_SCHEMA_SQL)
    audit_conn.commit()
    audit_conn.execute("PRAGMA query_only=1")

    state = AppState(
        rule_index=rule_index,
        scenario_index={},
        skill_index=discover(),
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
    register_events_routes(app)
    app.add_middleware(_BodyCapMiddleware, max_request_body_bytes=cfg.MAX_REQUEST_BODY_BYTES)
    app.add_middleware(RequestIDMiddleware)
    app.state.engine = state
    return app, state


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_post_events_receipt_round_trip_verify_signature_valid(
    tmp_path: pathlib.Path,
) -> None:
    """Receipt sign → persist → fetch → verify round-trip.

    Posts one deterministic-path event (ACC-008), fetches the stored receipt
    by receipt_id, then calls verify_signature with the config signing key.
    Result must be VALID — the full sign/persist/verify chain is exercised.
    """
    import anyio

    from samantha_server.api.app import _consume

    app, state = _build_app(tmp_path)

    async def _impl() -> None:
        state.consumer_task = asyncio.create_task(_consume(state), name="e2e-round-trip-consumer")
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                with patch(
                    "samantha_server.api.rbac._get_rbac_hmac_key",
                    return_value=_EVENTS_TEST_KEY,
                ):
                    resp = await client.post(
                        "/events",
                        json=_make_step1_body(),
                        headers=_make_auth_headers(),
                    )

            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            receipt_id = data["receipt_id"]
            assert receipt_id, "receipt_id must be non-empty"

            # Fetch the stored receipt payload from SQLite via the write connection.
            # The receipt_writer holds the write connection; use a fresh read connection
            # on the same file to mirror the audit path.
            db_path = tmp_path / "receipts.db"
            read_conn = sqlite3.connect(str(db_path))
            try:
                row = read_conn.execute(
                    "SELECT receipt_id, signer_key_id, signature, payload_json, signed_at_utc "
                    "FROM receipts WHERE receipt_id = ?",
                    (receipt_id,),
                ).fetchone()
            finally:
                read_conn.close()

            assert row is not None, f"No receipt row for receipt_id={receipt_id!r}"
            _receipt_id, signer_key_id, signature_bytes, payload_json_str, _ = row

            # Rehydrate the SignedReceipt from the stored payload_json.
            from datetime import UTC, datetime

            from samantha_server.engine.decision import EngineDecision
            from samantha_server.receipts.signing import SignedReceipt

            decision = EngineDecision.model_validate(json.loads(payload_json_str))
            receipt = SignedReceipt(
                receipt_id=receipt_id,
                decision=decision,
                signer_key_id=signer_key_id,
                signature=signature_bytes,
                signed_at_utc=datetime.now(tz=UTC),  # timestamp not used in verify
            )

            # verify_signature must return VALID with the config current key.
            import samantha_server.config as cfg
            from samantha_server.receipts.signing import VerificationResult, verify_signature

            result = verify_signature(
                receipt,
                current_key=cfg.RECEIPT_SIGNING_KEY,
                current_key_id=cfg.RECEIPT_SIGNING_KEY_ID,
            )
            assert result == VerificationResult.VALID, (
                f"Expected VALID, got {result!r} for receipt_id={receipt_id!r}"
            )
        finally:
            await state.aclose()

    anyio.run(_impl)
