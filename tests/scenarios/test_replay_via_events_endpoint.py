"""Phase B, Step 5 — tests for the replay-via-events-endpoint rewrite.

These tests verify the transport rewrite where every replay step is submitted
through POST /events → priority queue → _consume → dispatch_event, rather than
calling dispatch_event or evaluate() directly in-process.

Test structure (one test per slice, in order):

Slice 1: EngineDecision round-trip losslessness
  - decision.model_dump(mode="json") → model_validate round-trips all
    fields the harness reads without data loss.

Slice 2: _ReplayHarness builds a working app+queue+consumer
  - The context manager starts _consume, accepts a POST /events, and
    returns a 200 with a valid decision.

Slice 3: Stub LLM path for deterministic-only replay
  - has_llm_path=False uses a no-op stub so no oMLX/MLX load occurs.

Slice 4: Per-step POST rewrite in _replay_scenario_async
  - A deterministic 2-step scenario routes through the endpoint and
    produces correct verdicts (pass/fail/flags) matching direct evaluate().
"""

from __future__ import annotations

import asyncio
import pathlib
import sqlite3
import time
from typing import Any
from unittest.mock import MagicMock

from samantha_server.api.app import RequestIDMiddleware, _BodyCapMiddleware
from samantha_server.api.events import register_events_routes
from samantha_server.api.lifespan import AppState
from samantha_server.api.rbac import register_rbac_exception_handlers
from samantha_server.api.receipt_writer import ReceiptWriter
from samantha_server.engine.decision import EngineDecision
from samantha_server.llm.client import LLMResponse
from samantha_server.observability.cached_probe import make_langfuse_stub_probe
from samantha_server.observability.counters import CounterRegistry
from samantha_server.queue.priority import PriorityEventQueue
from samantha_server.receipts.store import _SCHEMA_SQL
from samantha_server.rules.loader import RuleIndex, load_rule_specs
from samantha_server.skills.loader import discover

# ---------------------------------------------------------------------------
# RBAC test key (mirrors test_replay_through_events.py)
# ---------------------------------------------------------------------------

_EVENTS_TEST_KEY = bytes.fromhex("cafebabe" + "deadbeef" * 6 + "cafebabe")


def _make_events_submit_token() -> str:
    from samantha_server.api.rbac import _sign_token

    now = int(time.time())
    return _sign_token("events:submit", now, now + 3600, hmac_key=_EVENTS_TEST_KEY)


def _make_auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_events_submit_token()}"}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SPECS_DIR = pathlib.Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"


def _build_rule_index() -> RuleIndex:
    return RuleIndex(load_rule_specs(_SPECS_DIR))


def _make_stub_llm_client() -> MagicMock:
    """Return a no-op mock LLMClient (never called on deterministic paths)."""
    mock = MagicMock()
    mock.model_id = "stub-model"
    mock.complete.return_value = LLMResponse(
        text="stub",
        input_tokens=1,
        output_tokens=1,
        model_id="stub-model",
        latency_us=100,
    )
    return mock


def _build_app_state(tmp: pathlib.Path, llm_client: Any | None = None) -> AppState:
    if llm_client is None:
        llm_client = _make_stub_llm_client()
    rule_index = _build_rule_index()
    receipt_writer = ReceiptWriter.open(tmp / "receipts.db")
    audit_conn = sqlite3.connect(":memory:", check_same_thread=False)
    audit_conn.executescript(_SCHEMA_SQL)
    audit_conn.commit()
    audit_conn.execute("PRAGMA query_only=1")
    return AppState(
        rule_index=rule_index,
        scenario_index={},
        skill_index=discover(),
        llm_client=llm_client,
        receipt_writer=receipt_writer,
        receipt_write_lock=asyncio.Lock(),
        counters=CounterRegistry(),
        langfuse_probe=make_langfuse_stub_probe(),
        commit_sha="test-sha",
        queue=PriorityEventQueue(maxsize=256),
        receipt_audit_conn=audit_conn,
        is_draining=False,
    )


def _build_test_app(state: AppState) -> Any:
    from fastapi import FastAPI

    import samantha_server.config as cfg

    app = FastAPI()
    register_rbac_exception_handlers(app)
    register_events_routes(app)
    app.add_middleware(_BodyCapMiddleware, max_request_body_bytes=cfg.MAX_REQUEST_BODY_BYTES)
    app.add_middleware(RequestIDMiddleware)
    app.state.engine = state
    return app


def _event_body(
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


# ACC-008 scenario data (mirrors SC-001 from test_replay_through_events.py)
_ORDER: dict[str, Any] = {
    "order_id": "REPLAY-TEST-001",
    "patient_name": "TESTPATIENT-0001, Michael",
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

_STEP1_EVENT_TYPE = "order_received"
_STEP1_EVENT_DATA: dict[str, Any] = {
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


# ---------------------------------------------------------------------------
# Slice 1: EngineDecision model_dump(mode="json") → model_validate round-trip
# ---------------------------------------------------------------------------


def test_engine_decision_json_round_trip_lossless() -> None:
    """decision.model_dump(mode='json') → EngineDecision.model_validate preserves
    all fields the harness reads: next_state, applied_rule_id, flags_added,
    flags_cleared, latency_us, decision_traces, primitive_traces, also_matched.

    This is the contract the endpoint response body relies on: events.py
    serialises with mode='json' and the harness reconstructs via model_validate.
    """
    from samantha_server.engine.dispatcher import list_applicable_rules
    from samantha_server.engine.evaluator import evaluate
    from samantha_server.models.context import Event, Order, SpecimenContext

    rule_index = _build_rule_index()

    order = Order(
        order_id="ROUND-TRIP-001",
        patient_name="TEST, Alice",
        patient_sex="F",
        age=45,
        specimen_type="biopsy",
        anatomic_site="breast",
        fixative="formalin",
        fixation_time_hours=24.0,
        ordered_tests=("Breast IHC Panel",),
        priority="routine",
        billing_info_present=True,
    )
    event = Event(
        event_type="order_received",
        event_data=dict(_STEP1_EVENT_DATA),
        step_index=1,
    )
    ctx = SpecimenContext(order=order, current_state="ACCESSIONING", flags=frozenset(), event=event)

    dispatch = list_applicable_rules(ctx, rule_index, session_id="ROUND-TRIP-001", ttl_sec=3600)
    original = evaluate(dispatch, ctx, session_id="ROUND-TRIP-001")

    # Simulate the endpoint serialisation path
    dumped = original.model_dump(mode="json")
    reconstructed = EngineDecision.model_validate(dumped)

    # Fields the harness reads after endpoint dispatch — all must survive the round-trip.
    assert reconstructed.next_state == original.next_state
    assert reconstructed.applied_rule_id == original.applied_rule_id
    assert reconstructed.flags_added == original.flags_added
    assert reconstructed.flags_cleared == original.flags_cleared
    assert reconstructed.latency_us == original.latency_us
    assert reconstructed.also_matched == original.also_matched
    assert reconstructed.decision_traces == original.decision_traces
    assert reconstructed.outcome == original.outcome
    assert reconstructed.dispatched_rule_ids == original.dispatched_rule_ids

    # primitive_traces: PrimitiveTrace.expected/actual fields are typed Any.
    # Frozenset and tuple values become lists after mode="json" serialisation —
    # Pydantic cannot coerce them back without a type annotation. The harness
    # does NOT read primitive_traces after dispatch (only receipt persistence
    # reads it downstream). Document the known lossy round-trip here so a
    # future harness extension that does read primitive_traces knows to handle
    # the type coercion explicitly.
    assert set(reconstructed.primitive_traces.keys()) == set(original.primitive_traces.keys()), (
        "primitive_traces keys must survive round-trip "
        "(values may differ in frozenset→list coercion)"
    )


# ---------------------------------------------------------------------------
# Slice 2: _ReplayHarness builds a working app+queue+consumer
# ---------------------------------------------------------------------------


def test_replay_harness_context_manager_accepts_post_and_returns_200(
    tmp_path: pathlib.Path,
) -> None:
    """_ReplayHarness starts _consume, accepts a POST /events, and returns 200 with
    a valid decision dict containing next_state and applied_rule_id.

    This validates the helper that replay() will use instead of _build_replay_deps.
    """
    import anyio

    from samantha_server.scenarios.replay import _ReplayHarness

    async def _impl() -> None:
        async with _ReplayHarness(
            rule_index=_build_rule_index(),
            has_llm_path=False,
            llm_client=None,
            receipts_db_path=None,
            scenarios=[],
        ) as harness:
            # harness patches _get_rbac_hmac_key with its own key;
            # make_auth_headers() signs tokens with the same key.
            body = _event_body(
                order=_ORDER,
                current_state="ACCESSIONING",
                flags=[],
                event_type=_STEP1_EVENT_TYPE,
                event_data=_STEP1_EVENT_DATA,
                session_id="HARNESS-TEST-001",
                step_index=1,
            )
            resp = await harness.client.post(
                "/events", json=body, headers=harness.make_auth_headers()
            )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "decision" in data
        assert data["decision"]["next_state"] == "ACCEPTED"
        assert data["decision"]["applied_rule_id"] == "ACC-008"

    anyio.run(_impl)


# ---------------------------------------------------------------------------
# Slice 3: Stub LLM path — deterministic-only corpus never loads oMLX
# ---------------------------------------------------------------------------


def test_replay_harness_stub_llm_used_when_no_llm_path(
    tmp_path: pathlib.Path,
) -> None:
    """When has_llm_path=False, _ReplayHarness injects a no-op stub LLM client.

    The stub is never called during a deterministic-only replay; this test
    verifies the harness starts cleanly and the stub is not None.
    """
    import anyio

    from samantha_server.scenarios.replay import _ReplayHarness

    captured_llm_client: list[Any] = []

    async def _impl() -> None:
        async with _ReplayHarness(
            rule_index=_build_rule_index(),
            has_llm_path=False,
            llm_client=None,
            receipts_db_path=None,
            scenarios=[],
        ) as harness:
            captured_llm_client.append(harness.state.llm_client)

    anyio.run(_impl)
    stub = captured_llm_client[0]
    assert stub is not None, "Stub LLM client must not be None"
    # Verify it has a model_id so routing internals don't blow up
    _ = stub.model_id


# ---------------------------------------------------------------------------
# Slice 4: Per-step POST in _replay_scenario_async (deterministic 2-step)
# ---------------------------------------------------------------------------


def _make_sc001_scenario() -> Any:
    """Build the SC-001 scenario as a Scenario object (2 deterministic steps)."""
    from samantha_server.scenarios.loader import Scenario, ScenarioStep

    return Scenario(
        scenario_id="SC-001",
        category="rule_coverage",
        description="order_received → ACCEPTED → SAMPLE_PREP_PROCESSING",
        steps=(
            ScenarioStep(
                step_index=1,
                event_type="order_received",
                event_data={
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
                expected_next_state="ACCEPTED",
                expected_applied_rules=("ACC-008",),
                expected_flags=(),
                expected_routing_path=None,
                llm_disposition=None,
            ),
            ScenarioStep(
                step_index=2,
                event_type="grossing_complete",
                event_data={"outcome": "success"},
                expected_next_state="SAMPLE_PREP_PROCESSING",
                expected_applied_rules=("SP-001",),
                expected_flags=(),
                expected_routing_path=None,
                llm_disposition=None,
            ),
        ),
        user_role=None,
        prompt_timestamp=None,
    )


def test_replay_scenario_async_via_endpoint_passes_sc001(
    tmp_path: pathlib.Path,
) -> None:
    """_replay_scenario_async (endpoint path) produces passing verdicts for SC-001.

    Both steps must be status='pass', routing_path='deterministic', and
    next_state/applied_rule_id must match the fixture expectations.

    This is the core regression gate: the same scenario that the direct
    dispatch_event path used to produce must still pass through the endpoint.
    """
    import anyio

    from samantha_server.scenarios.replay import _replay_scenario_async, _ReplayHarness

    scenario = _make_sc001_scenario()
    rule_index = _build_rule_index()

    async def _impl() -> None:
        async with _ReplayHarness(
            rule_index=rule_index,
            has_llm_path=False,
            llm_client=None,
            receipts_db_path=None,
            scenarios=[],
        ) as harness:
            # harness.__aenter__ already patches _get_rbac_hmac_key with its
            # own RBAC key; make_auth_headers() signs tokens with the same key.
            verdict = await _replay_scenario_async(
                scenario,
                rule_index,
                harness=harness,
            )

        assert verdict.status == "pass", (
            f"Expected 'pass', got {verdict.status!r}. "
            f"Steps: {[(sv.step_index, sv.status) for sv in verdict.step_verdicts]}"
        )
        assert len(verdict.step_verdicts) == 2

        sv1 = verdict.step_verdicts[0]
        assert sv1.status == "pass", f"Step 1 status: {sv1.status}"
        assert sv1.routing_path == "deterministic"
        assert sv1.predicted.next_state == "ACCEPTED"
        assert sv1.predicted.applied_rules == ("ACC-008",)

        sv2 = verdict.step_verdicts[1]
        assert sv2.status == "pass", f"Step 2 status: {sv2.status}"
        assert sv2.routing_path == "deterministic"
        assert sv2.predicted.next_state == "SAMPLE_PREP_PROCESSING"
        assert sv2.predicted.applied_rules == ("SP-001",)

    anyio.run(_impl)


def test_replay_scenario_async_via_endpoint_state_mismatch_detected(
    tmp_path: pathlib.Path,
) -> None:
    """_replay_scenario_async (endpoint path) detects state mismatches.

    A scenario with wrong expected_next_state must produce status='mismatch_state'.
    This verifies that the verdict comparison still works through the endpoint path.
    """
    import anyio

    from samantha_server.scenarios.loader import Scenario, ScenarioStep
    from samantha_server.scenarios.replay import (
        ScenarioVerdict,
        _replay_scenario_async,
        _ReplayHarness,
    )

    scenario = Scenario(
        scenario_id="MISMATCH-001",
        category="rule_coverage",
        description="wrong expected state",
        steps=(
            ScenarioStep(
                step_index=1,
                event_type="order_received",
                event_data={
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
                expected_next_state="DO_NOT_PROCESS",  # wrong — engine returns ACCEPTED
                expected_applied_rules=("ACC-008",),
                expected_flags=(),
                expected_routing_path=None,
                llm_disposition=None,
            ),
        ),
        user_role=None,
        prompt_timestamp=None,
    )

    rule_index = _build_rule_index()

    async def _impl() -> ScenarioVerdict:
        async with _ReplayHarness(
            rule_index=rule_index,
            has_llm_path=False,
            llm_client=None,
            receipts_db_path=None,
            scenarios=[],
        ) as harness:
            return await _replay_scenario_async(
                scenario,
                rule_index,
                harness=harness,
            )

    verdict = anyio.run(_impl)
    assert verdict.status == "fail"
    assert verdict.step_verdicts[0].status == "mismatch_state"


# ---------------------------------------------------------------------------
# Slice 5: replay() deterministic-subset parity (the core regression gate)
# ---------------------------------------------------------------------------


def test_replay_deterministic_subset_parity_via_endpoint() -> None:
    """replay() via endpoint path achieves high parity for deterministic-category scenarios.

    This is the primary safety net for the transport rewrite. All rule_coverage,
    multi_rule, and accumulated_state scenarios in the vendored corpus are exercised
    through POST /events → queue → _consume → dispatch_event (the real production path).

    Full parity is required: the endpoint path must match the corpus expectations for
    every deterministic scenario, with no carve-outs. (surfaced one apparent
    divergence — SC-115's off-vocab anatomic_site="tibia" — which turned out to be a
    production bug: preflight was shadowing ACC-011. Fixed by omitting anatomic_site
    from preflight's CANONICALIZED_ORDER_FIELDS, so ACC-011 routes it to
    PENDING_LLM_REVIEW as designed. No divergence remains.)

    LLM-path categories (query/unknown_input/hallucination/llm_review) are
    excluded — they require live oMLX which is not available in CI.
    """
    import json

    from samantha_server.scenarios.replay import _DETERMINISTIC_CATEGORIES, replay

    vendored_dir = pathlib.Path(__file__).parents[1] / "fixtures" / "scenarios"
    assert vendored_dir.exists(), f"Vendored corpus not found at {vendored_dir}"

    # Collect how many deterministic scenarios exist
    det_scenarios = []
    for cat in _DETERMINISTIC_CATEGORIES:
        cat_dir = vendored_dir / cat
        if cat_dir.is_dir():
            for f in cat_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                    det_scenarios.append(data.get("scenario_id", f.stem))
                except Exception:
                    pass

    assert len(det_scenarios) > 0, "No deterministic scenarios found"

    # The harness patches _get_rbac_hmac_key internally — no external patch needed.
    report = replay(
        vendored_dir,
        include_categories=_DETERMINISTIC_CATEGORIES,
    )

    failures = [
        v
        for v in report.scenario_verdicts
        if v.status == "fail" and v.category in _DETERMINISTIC_CATEGORIES
    ]
    failure_details = []
    for v in failures:
        for sv in v.step_verdicts:
            if sv.status != "pass":
                failure_details.append(
                    f"  [{v.category}] {v.scenario_id} step {sv.step_index}: {sv.status}\n"
                    f"    expected: state={sv.expected.next_state} "
                    f"rules={list(sv.expected.applied_rules)}\n"
                    f"    predicted: state={sv.predicted.next_state} "
                    f"rules={list(sv.predicted.applied_rules)}"
                )

    assert len(failures) == 0, (
        f"Deterministic parity: {len(failures)} failure(s) on the endpoint path:\n"
        + "\n".join(failure_details)
    )
    assert report.included_accuracy == 1.0, (
        f"Deterministic parity accuracy {report.included_accuracy:.4%} != 100% — "
        f"a scenario regressed on the endpoint path.\n"
        f"{len(failures)} failures:\n" + "\n".join(failure_details)
    )


# ---------------------------------------------------------------------------
# Slice 6: prompt_timestamp forwarded by the replay harness endpoint branch
# ---------------------------------------------------------------------------


def test_replay_endpoint_branch_forwards_prompt_timestamp_string(
    tmp_path: pathlib.Path,
) -> None:
    """Replay endpoint branch includes prompt_timestamp in POST body.

    When a scenario has a non-None prompt_timestamp, the endpoint branch must
    include "prompt_timestamp" in the POST /events body so that EventRequest
    receives it and forwards it verbatim to the queued payload.

    Strategy: patch _ReplayHarness.client.post to capture the JSON body, then
    verify "prompt_timestamp" key is present with the scenario's value.
    """
    import anyio

    from samantha_server.scenarios.loader import Scenario, ScenarioStep
    from samantha_server.scenarios.replay import _replay_scenario_async, _ReplayHarness

    scenario = Scenario(
        scenario_id="PT-001",
        category="rule_coverage",
        description="order_received with fixture prompt_timestamp",
        steps=(
            ScenarioStep(
                step_index=1,
                event_type="order_received",
                event_data={
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
                expected_next_state="ACCEPTED",
                expected_applied_rules=("ACC-008",),
                expected_flags=(),
                expected_routing_path=None,
                llm_disposition=None,
            ),
        ),
        user_role=None,
        prompt_timestamp="2025-01-16T09:00:00Z",
    )

    rule_index = _build_rule_index()
    captured_bodies: list[dict[str, Any]] = []

    async def _impl() -> None:
        async with _ReplayHarness(
            rule_index=rule_index,
            has_llm_path=False,
            llm_client=None,
            receipts_db_path=None,
            scenarios=[],
        ) as harness:
            original_post = harness.client.post

            async def _capturing_post(url: str, **kwargs: Any) -> Any:
                body = kwargs.get("json", {})
                captured_bodies.append(body)
                return await original_post(url, **kwargs)

            harness.client.post = _capturing_post  # type: ignore[method-assign]
            await _replay_scenario_async(scenario, rule_index, harness=harness)

    anyio.run(_impl)

    assert len(captured_bodies) == 1, "Expected exactly one POST /events call"
    body = captured_bodies[0]
    assert "prompt_timestamp" in body, (
        "replay endpoint branch must include 'prompt_timestamp' key in POST body"
    )
    assert body["prompt_timestamp"] == "2025-01-16T09:00:00Z", (
        f"prompt_timestamp mismatch: got {body['prompt_timestamp']!r}"
    )


def test_replay_endpoint_branch_forwards_null_prompt_timestamp(
    tmp_path: pathlib.Path,
) -> None:
    """Replay endpoint branch includes prompt_timestamp=null in POST body.

    When a scenario has prompt_timestamp=None, the endpoint branch must still
    include "prompt_timestamp": null in the POST body (key present, value null).
    This allows EventRequest to detect the field as explicitly provided and
    forward None to the queued payload — suppressing the now() fallback.
    """
    import anyio

    from samantha_server.scenarios.loader import Scenario, ScenarioStep
    from samantha_server.scenarios.replay import _replay_scenario_async, _ReplayHarness

    scenario = Scenario(
        scenario_id="PT-002",
        category="rule_coverage",
        description="order_received with no prompt_timestamp (None)",
        steps=(
            ScenarioStep(
                step_index=1,
                event_type="order_received",
                event_data={
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
                expected_next_state="ACCEPTED",
                expected_applied_rules=("ACC-008",),
                expected_flags=(),
                expected_routing_path=None,
                llm_disposition=None,
            ),
        ),
        user_role=None,
        prompt_timestamp=None,  # No anchor
    )

    rule_index = _build_rule_index()
    captured_bodies: list[dict[str, Any]] = []

    async def _impl() -> None:
        async with _ReplayHarness(
            rule_index=rule_index,
            has_llm_path=False,
            llm_client=None,
            receipts_db_path=None,
            scenarios=[],
        ) as harness:
            original_post = harness.client.post

            async def _capturing_post(url: str, **kwargs: Any) -> Any:
                body = kwargs.get("json", {})
                captured_bodies.append(body)
                return await original_post(url, **kwargs)

            harness.client.post = _capturing_post  # type: ignore[method-assign]
            await _replay_scenario_async(scenario, rule_index, harness=harness)

    anyio.run(_impl)

    assert len(captured_bodies) == 1, "Expected exactly one POST /events call"
    body = captured_bodies[0]
    assert "prompt_timestamp" in body, (
        "replay endpoint branch must include 'prompt_timestamp' key even when value is None"
    )
    assert body["prompt_timestamp"] is None, (
        f"prompt_timestamp must be null for None scenario; got {body['prompt_timestamp']!r}"
    )


# ---------------------------------------------------------------------------
# Slice 7: receipt-read path delivers non-empty primitive_traces
# ---------------------------------------------------------------------------


def test_receipt_read_path_delivers_non_empty_primitive_traces(
    tmp_path: pathlib.Path,
) -> None:
    """The receipt-read path (not the WARNING empty-trace fallback) must populate
    primitive_traces for a rule-firing step.

    Strategy: POST one ACC-008 order_received event through the harness, then
    read the persisted receipt directly via ReceiptWriter.fetch_payload_json
    and assert primitive_traces is non-empty.  This proves the receipt was
    written and can be read back through the protocol method, not via a private
    _conn reach-in.

    If the WARNING fallback fires instead, the receipt would be missing from the
    store (because no real receipt was persisted), so fetch_payload_json returns
    None.  The assertion below catches that case explicitly.
    """
    import json

    import anyio

    from samantha_server.scenarios.replay import _ReplayHarness

    captured_receipt_id: list[str] = []
    captured_writer: list[Any] = []

    async def _impl() -> None:
        async with _ReplayHarness(
            rule_index=_build_rule_index(),
            has_llm_path=False,
            llm_client=None,
            receipts_db_path=None,
            scenarios=[],
        ) as harness:
            body = _event_body(
                order=_ORDER,
                current_state="ACCESSIONING",
                flags=[],
                event_type=_STEP1_EVENT_TYPE,
                event_data=_STEP1_EVENT_DATA,
                session_id="RECEIPT-READ-TEST-001",
                step_index=1,
            )
            resp = await harness.client.post(
                "/events", json=body, headers=harness.make_auth_headers()
            )
            assert resp.status_code == 200, f"POST /events failed: {resp.text}"
            data = resp.json()
            captured_receipt_id.append(data["receipt_id"])

            # Fetch the payload_json via the public method on the writer protocol.
            # This is the same call the replay loop must use after the fix.
            payload_json = harness.state.receipt_writer.fetch_payload_json(data["receipt_id"])
            assert payload_json is not None, (
                "ReceiptWriter.fetch_payload_json must return the persisted payload "
                f"for receipt_id={data['receipt_id']!r}. "
                "A None result means the WARNING empty-trace fallback fired instead of "
                "the receipt-read path — the receipt was never written or the wrong "
                "connection was used."
            )
            parsed = json.loads(payload_json)
            captured_writer.append(parsed)

    anyio.run(_impl)

    assert len(captured_writer) == 1, "Expected exactly one receipt payload"
    parsed = captured_writer[0]

    # ACC-008 fires on order_received with a complete order — primitive_traces must
    # be non-empty.  An empty dict here means the WARNING fallback reconstructed
    # the decision from the PHI-stripped /events response instead of the receipt.
    assert parsed.get("primitive_traces"), (
        "primitive_traces must be non-empty for an ACC-008 rule-firing step. Got: {!r}".format(
            parsed.get("primitive_traces")
        )
    )
