"""Tests for Slice 2: RES-002 billing-branch flag correction in dispatch_event.

The production deterministic branch previously passed ctx.flags (incoming flags)
to resolve_transition, causing RESOLVE_MISSING_INFO to resolve incorrectly when
the action handler would clear MISSING_INFO_PROCEED.

These tests drive an RES-002 ctx through dispatch_event and assert that:
- billing info_type: resolved next_state == "RESULTING" (MISSING_INFO_PROCEED cleared)
- non-billing info_type: resolved next_state == "RESULTING_HOLD" (flag preserved)

Both assertions also confirm the signed receipt carries the same resolved next_state.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.queue.priority import EventPriority
from samantha_server.receipts.signing import SignedReceipt

_SPECS_DIR = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"


def _make_rule_index() -> Any:
    from samantha_server.rules.loader import RuleIndex, load_rule_specs

    return RuleIndex(load_rule_specs(_SPECS_DIR))


def _make_mock_llm() -> Any:
    from unittest.mock import MagicMock

    from samantha_server.llm.client import LLMResponse
    from tests.api.helpers import _CANNED_JSON_TEXT

    mock = MagicMock()
    mock.model_id = "test-model"
    mock.complete.return_value = LLMResponse(
        text="Test response.",
        input_tokens=10,
        output_tokens=5,
        model_id="test-model",
        latency_us=1000,
    )
    mock.complete_json.return_value = LLMResponse(
        text=_CANNED_JSON_TEXT,
        input_tokens=10,
        output_tokens=5,
        model_id="test-model",
        latency_us=1000,
    )
    return mock


def _make_res002_ctx(*, info_type: str, order_id: str = "GH328-RES002") -> SpecimenContext:
    """Return a ctx where RES-002 fires.

    current_state=RESULTING_HOLD + flags={MISSING_INFO_PROCEED} +
    event_type=missing_info_received + event_data.info_type=<info_type>.

    RES-002 predicate: Not(IsNull(event.info_type)) — fires when info_type is non-null.
    RESULTING_HOLD maps to step RESULTING via STATE_TO_STEP, so RES-002 dispatches.
    """
    return SpecimenContext(
        order=Order(
            order_id=order_id,
            patient_name="Jane Doe",
            patient_sex="F",
            age=55,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="RESULTING_HOLD",
        flags=frozenset({"MISSING_INFO_PROCEED"}),
        event=Event(
            event_type="missing_info_received",
            event_data={"info_type": info_type, "value": "some-value"},
            step_index=0,
        ),
    )


def _make_deps(*, rule_index: Any) -> dict[str, Any]:
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.skills.loader import discover
    from tests.api.helpers import SpyReceiptWriter

    written: list[SignedReceipt] = []
    return {
        "receipt_writer": SpyReceiptWriter(written),
        "write_lock": asyncio.Lock(),
        "counters": CounterRegistry(),
        "llm_client": _make_mock_llm(),
        "scenarios_index": {},
        "skills_index": discover(),
        "rule_index": rule_index,
        "_written": written,
    }


# ---------------------------------------------------------------------------
# Billing branch resolves RESOLVE_MISSING_INFO → RESULTING
# ---------------------------------------------------------------------------


def test_res002_billing_info_resolves_to_resulting() -> None:
    """Billing info_type clears MISSING_INFO_PROCEED → RESULTING.

    Pre-fix: accumulated_flags=ctx.flags (still has MISSING_INFO_PROCEED) →
    resolve_transition returns RESULTING_HOLD (wrong).
    Post-fix: post-action-handler flags (MISSING_INFO_PROCEED cleared) →
    resolve_transition returns RESULTING (correct).
    """
    from samantha_server.api.routing import dispatch_event

    ctx = _make_res002_ctx(info_type="billing", order_id="GH328-BILLING")
    rule_index = _make_rule_index()
    deps = _make_deps(rule_index=rule_index)
    written = deps.pop("_written")

    async def run() -> tuple[Any, Any, Any]:
        return await dispatch_event(
            ctx,
            session_id="gh328-billing",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            **deps,
        )

    decision, dispatch_ctx, receipt = asyncio.run(run())

    assert dispatch_ctx.routing_path == "deterministic"
    assert decision.applied_rule_id == "RES-002", (
        f"Expected RES-002 to fire, got {decision.applied_rule_id!r}"
    )
    assert decision.next_state == "RESULTING", (
        f"Expected 'RESULTING' (billing cleared MISSING_INFO_PROCEED), got {decision.next_state!r}"
    )
    assert len(written) == 1
    assert written[0].decision.next_state == "RESULTING", (
        f"Receipt stamped wrong next_state: {written[0].decision.next_state!r}"
    )


def test_res002_non_billing_info_resolves_to_resulting_hold() -> None:
    """Non-billing info_type preserves MISSING_INFO_PROCEED → RESULTING_HOLD.

    The action handler does NOT clear MISSING_INFO_PROCEED for clinical_notes,
    so resolve_transition returns RESULTING_HOLD (hold continues).
    """
    from samantha_server.api.routing import dispatch_event

    ctx = _make_res002_ctx(info_type="clinical_notes", order_id="GH328-NONBILLING")
    rule_index = _make_rule_index()
    deps = _make_deps(rule_index=rule_index)
    written = deps.pop("_written")

    async def run() -> tuple[Any, Any, Any]:
        return await dispatch_event(
            ctx,
            session_id="gh328-nonbilling",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            **deps,
        )

    decision, dispatch_ctx, receipt = asyncio.run(run())

    assert dispatch_ctx.routing_path == "deterministic"
    assert decision.applied_rule_id == "RES-002", (
        f"Expected RES-002 to fire, got {decision.applied_rule_id!r}"
    )
    assert decision.next_state == "RESULTING_HOLD", (
        f"Expected 'RESULTING_HOLD' (non-billing preserves MISSING_INFO_PROCEED), "
        f"got {decision.next_state!r}"
    )
    assert len(written) == 1
    assert written[0].decision.next_state == "RESULTING_HOLD", (
        f"Receipt stamped wrong next_state: {written[0].decision.next_state!r}"
    )
