"""GH-324 Phase B, Step 4 — integration characterization test.

Proves that a fully deterministic scenario (SC-001) driven step-by-step through
the REAL production transport — POST /events → priority queue → _consume consumer
task → dispatch_event → emit_receipt — produces verdicts matching the fixture's
expected_output AND persists a signed receipt with applied_rule_id set and
primitive_traces populated.

Key design decisions:
- Uses the REAL _consume task (asyncio.create_task), NOT a patched enqueue_or_reject.
- Drives requests via httpx.AsyncClient(ASGITransport) so the event loop hosts both
  the consumer task and the HTTP client in the same asyncio run.
- Builds AppState manually (like _make_test_app in test_events_endpoint.py) but
  loads the real RuleIndex from rules/specs/ so ACC-008 and SP-001 fire.
- ReceiptWriter opens at a tmpdir path; conftest already sets RECEIPTS_DB_PATH to
  a /tmp path so _validate_store_path(allowed_base=Path(RECEIPTS_DB_PATH).parent)
  accepts a sibling path in the same /tmp parent.
- primitive_traces live in the signed receipt's decision field (EngineDecision),
  stored as payload_json in the SQLite receipts table. The test reads them from
  that persisted payload, NOT from the HTTP response: /events strips
  primitive_traces + decision_traces from the wire (PHI), mirroring /receipts.
- Uses anyio.run(_impl) inside a sync test function, matching the project's
  established pattern (tests/scenarios/test_replay_via_events_endpoint.py).
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

from samantha_server.api.app import RequestIDMiddleware, _BodyCapMiddleware, _consume
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

# ---------------------------------------------------------------------------
# RBAC test key (mirrors test_events_endpoint.py)
# ---------------------------------------------------------------------------

_EVENTS_TEST_KEY = bytes.fromhex("cafebabe" + "deadbeef" * 6 + "cafebabe")


def _make_events_submit_token() -> str:
    from samantha_server.api.rbac import _sign_token

    now = int(time.time())
    return _sign_token("events:submit", now, now + 3600, hmac_key=_EVENTS_TEST_KEY)


def _make_auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_events_submit_token()}"}


# ---------------------------------------------------------------------------
# SC-001 fixture — loaded inline (avoids importing the scenario loader machinery)
# ---------------------------------------------------------------------------

# Step 1: order_received → ACCEPTED via ACC-008
_SC001_STEP1_EVENT_TYPE: str = "order_received"
_SC001_STEP1_EVENT_DATA: dict[str, Any] = {
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
}
_SC001_STEP1_EXPECTED_NEXT_STATE: str = "ACCEPTED"
_SC001_STEP1_EXPECTED_RULE: str = "ACC-008"
_SC001_STEP1_EXPECTED_FLAGS: list[str] = []

# Step 2: grossing_complete → SAMPLE_PREP_PROCESSING via SP-001
_SC001_STEP2_EVENT_TYPE: str = "grossing_complete"
_SC001_STEP2_EVENT_DATA: dict[str, Any] = {"outcome": "success"}
_SC001_STEP2_EXPECTED_NEXT_STATE: str = "SAMPLE_PREP_PROCESSING"
_SC001_STEP2_EXPECTED_RULE: str = "SP-001"

# The Order dict is built from step 1's event_data (mirrors replay._build_order).
# order_id uses the scenario_id per the harness convention.
_SC001_ORDER: dict[str, Any] = {
    "order_id": "SC-001",
    "patient_name": _SC001_STEP1_EVENT_DATA["patient_name"],
    "patient_sex": _SC001_STEP1_EVENT_DATA["sex"],
    "age": _SC001_STEP1_EVENT_DATA["age"],
    "specimen_type": _SC001_STEP1_EVENT_DATA["specimen_type"],
    "anatomic_site": _SC001_STEP1_EVENT_DATA["anatomic_site"],
    "fixative": _SC001_STEP1_EVENT_DATA["fixative"],
    "fixation_time_hours": _SC001_STEP1_EVENT_DATA["fixation_time_hours"],
    "ordered_tests": _SC001_STEP1_EVENT_DATA["ordered_tests"],
    "priority": _SC001_STEP1_EVENT_DATA["priority"],
    "billing_info_present": _SC001_STEP1_EVENT_DATA["billing_info_present"],
}


# ---------------------------------------------------------------------------
# App + AppState builder
# ---------------------------------------------------------------------------


def _build_app_state_with_real_rules(tmp: pathlib.Path) -> AppState:
    """Build an AppState with real RuleIndex and a ReceiptWriter at *tmp*.

    Uses a mock LLM client (SC-001 is fully deterministic; LLM is never called).
    The receipt_audit_conn is an in-memory SQLite connection with the same schema
    so the AppState is fully functional without writing to disk for reads.
    """
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

    # Read-only in-memory audit connection (not used by this test, but AppState requires it).
    audit_conn = sqlite3.connect(":memory:", check_same_thread=False)
    audit_conn.executescript(_SCHEMA_SQL)
    audit_conn.commit()
    audit_conn.execute("PRAGMA query_only=1")

    return AppState(
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


def _build_test_app(state: AppState) -> FastAPI:
    import samantha_server.config as cfg

    app = FastAPI()
    register_rbac_exception_handlers(app)
    register_events_routes(app)
    app.add_middleware(_BodyCapMiddleware, max_request_body_bytes=cfg.MAX_REQUEST_BODY_BYTES)
    app.add_middleware(RequestIDMiddleware)
    app.state.engine = state
    return app


def _make_event_body(
    *,
    order: dict[str, Any],
    current_state: str,
    flags: list[str],
    event_type: str,
    event_data: dict[str, Any],
    session_id: str,
    step_index: int,
) -> dict[str, Any]:
    return {
        "ctx": {
            "order": order,
            "current_state": current_state,
            "flags": flags,
            "event": {
                "event_type": event_type,
                "event_data": event_data,
                "step_index": step_index,
            },
        },
        "session_id": session_id,
        "priority": "ROUTINE",
    }


# ---------------------------------------------------------------------------
# The integration test
# ---------------------------------------------------------------------------


def test_sc001_deterministic_end_to_end_through_real_queue_and_consumer(
    tmp_path: pathlib.Path,
) -> None:
    """Drive SC-001 (2 deterministic steps) through the full production transport.

    Invariants verified:
    1. Each step returns HTTP 200.
    2. decision["next_state"] matches the fixture's expected_next_state.
    3. decision["applied_rule_id"] matches the fixture's expected_applied_rule.
    4. On step 1 (ACC-008): the HTTP response decision excludes primitive_traces
       and decision_traces (PHI boundary, mirrors /receipts).
    5. On step 1: a row for the returned receipt_id exists in the SQLite receipts
       table with applied_rule_id="ACC-008" and payload_json containing non-empty
       primitive_traces (the persisted receipt retains them for auditing).

    Uses anyio.run(_impl) inside a sync test function to match the project's
    established async-test pattern (tests/scenarios/test_replay_via_events_endpoint.py).
    """
    import anyio

    state = _build_app_state_with_real_rules(tmp_path)
    app = _build_test_app(state)

    async def _impl() -> None:
        # Start the REAL consumer task — the crux of this test.
        state.consumer_task = asyncio.create_task(
            _consume(state), name="priority-queue-consumer-test"
        )

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                with patch(
                    "samantha_server.api.rbac._get_rbac_hmac_key",
                    return_value=_EVENTS_TEST_KEY,
                ):
                    # ------------------------------------------------------------------
                    # Step 1: order_received → expected ACCEPTED via ACC-008
                    # ------------------------------------------------------------------
                    step1_body = _make_event_body(
                        order=_SC001_ORDER,
                        current_state="ACCESSIONING",
                        flags=[],
                        event_type=_SC001_STEP1_EVENT_TYPE,
                        event_data=_SC001_STEP1_EVENT_DATA,
                        session_id="SC-001",
                        step_index=1,
                    )
                    resp1 = await client.post(
                        "/events", json=step1_body, headers=_make_auth_headers()
                    )

                    assert resp1.status_code == 200, (
                        f"Step 1 expected 200, got {resp1.status_code}: {resp1.text}"
                    )
                    data1 = resp1.json()
                    assert "receipt_id" in data1
                    assert data1["receipt_id"], "receipt_id must be non-empty"

                    decision1: dict[str, Any] = data1["decision"]

                    # Structural verdict: next_state and applied_rule_id
                    assert decision1["next_state"] == _SC001_STEP1_EXPECTED_NEXT_STATE, (
                        f"Step 1 next_state mismatch: "
                        f"got {decision1['next_state']!r}, "
                        f"expected {_SC001_STEP1_EXPECTED_NEXT_STATE!r}"
                    )
                    assert decision1["applied_rule_id"] == _SC001_STEP1_EXPECTED_RULE, (
                        f"Step 1 applied_rule_id mismatch: "
                        f"got {decision1['applied_rule_id']!r}, "
                        f"expected {_SC001_STEP1_EXPECTED_RULE!r}"
                    )

                    # PHI boundary (mirrors /receipts): the HTTP response must NOT
                    # carry decision_traces / primitive_traces — they hold verbatim
                    # clinical strings. They live only in the persisted receipt.
                    assert "primitive_traces" not in decision1, (
                        "primitive_traces must be excluded from the /events response (PHI)"
                    )
                    assert "decision_traces" not in decision1, (
                        "decision_traces must be excluded from the /events response (PHI)"
                    )

                    # Verify persisted receipt row in the SQLite DB
                    receipt_id_1 = data1["receipt_id"]
                    db_path = tmp_path / "receipts.db"
                    read_conn = sqlite3.connect(str(db_path))
                    try:
                        row = read_conn.execute(
                            "SELECT applied_rule_id, payload_json "
                            "FROM receipts WHERE receipt_id = ?",
                            (receipt_id_1,),
                        ).fetchone()
                    finally:
                        read_conn.close()

                    assert row is not None, f"No receipt row found for receipt_id={receipt_id_1!r}"
                    db_applied_rule_id, payload_json_str = row
                    assert db_applied_rule_id == "ACC-008", (
                        f"DB applied_rule_id: got {db_applied_rule_id!r}, expected 'ACC-008'"
                    )

                    payload = json.loads(payload_json_str)
                    assert payload.get("primitive_traces"), (
                        "Persisted payload_json.primitive_traces must be non-empty for ACC-008"
                    )

                    # ------------------------------------------------------------------
                    # Step 2: grossing_complete → expected SAMPLE_PREP_PROCESSING via SP-001
                    # Thread forward with EXPECTED outputs from step 1 (mirrors replay harness).
                    # ------------------------------------------------------------------
                    step2_body = _make_event_body(
                        order=_SC001_ORDER,
                        current_state=_SC001_STEP1_EXPECTED_NEXT_STATE,  # "ACCEPTED"
                        flags=_SC001_STEP1_EXPECTED_FLAGS,
                        event_type=_SC001_STEP2_EVENT_TYPE,
                        event_data=_SC001_STEP2_EVENT_DATA,
                        session_id="SC-001",
                        step_index=2,
                    )
                    resp2 = await client.post(
                        "/events", json=step2_body, headers=_make_auth_headers()
                    )

                    assert resp2.status_code == 200, (
                        f"Step 2 expected 200, got {resp2.status_code}: {resp2.text}"
                    )
                    data2 = resp2.json()
                    decision2: dict[str, Any] = data2["decision"]

                    assert decision2["next_state"] == _SC001_STEP2_EXPECTED_NEXT_STATE, (
                        f"Step 2 next_state mismatch: "
                        f"got {decision2['next_state']!r}, "
                        f"expected {_SC001_STEP2_EXPECTED_NEXT_STATE!r}"
                    )
                    assert decision2["applied_rule_id"] == _SC001_STEP2_EXPECTED_RULE, (
                        f"Step 2 applied_rule_id mismatch: "
                        f"got {decision2['applied_rule_id']!r}, "
                        f"expected {_SC001_STEP2_EXPECTED_RULE!r}"
                    )
        finally:
            await state.aclose()

    anyio.run(_impl)
