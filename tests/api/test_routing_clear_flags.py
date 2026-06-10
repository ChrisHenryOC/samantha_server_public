"""Tests for Fix B: non-empty clear_flags exercised through dispatch_event.

The existing RES-002 routing tests drive the post_flags line with EMPTY spec deltas
(RES-002 has set_flags: [] / clear_flags: []). This test drives SP-007, which has
clear_flags: [RECUT_REQUESTED], so set(decision.flags_cleared) is non-empty and the
set-difference branch in _dispatch_event_core executes with real content.

Coverage intent: crash-guard + coverage of `(ctx.flags - set(decision.flags_cleared))`.
The expression's logic-inversion case is unobservable with the current rule catalog
because the SP-007 transition (ADVANCE_SAMPLE_PREP) does not branch on flags — so
clearing RECUT_REQUESTED does not change the resolved next_state. Full guard comes
with Phase B parity.
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


def _make_sp007_ctx(*, order_id: str = "GH328-SP007") -> SpecimenContext:
    """Return a ctx where SP-007 fires.

    SP-007 predicate: event_type=sectioning_complete, outcome=success, flags contains
    RECUT_REQUESTED. SAMPLE_PREP_SECTIONING maps to step SAMPLE_PREP via STATE_TO_STEP,
    so SP-007 dispatches. SP-007 clears RECUT_REQUESTED (non-empty clear_flags).
    """
    return SpecimenContext(
        order=Order(
            order_id=order_id,
            patient_name="Jane Doe",
            patient_sex="F",
            age=45,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="SAMPLE_PREP_SECTIONING",
        flags=frozenset({"RECUT_REQUESTED"}),
        event=Event(
            event_type="sectioning_complete",
            event_data={"outcome": "success"},
            step_index=0,
        ),
    )


def test_sp007_clear_flags_executes_through_dispatch_event() -> None:
    """SP-007 has clear_flags=[RECUT_REQUESTED]; driving it through
    dispatch_event exercises the `ctx.flags - set(decision.flags_cleared)` branch
    with real non-empty content.

    Assertions:
    - SP-007 fires (applied_rule_id).
    - Resolved next_state is SAMPLE_PREP_QC (ADVANCE_SAMPLE_PREP from SAMPLE_PREP_SECTIONING).
    - Exactly one receipt is emitted with the same resolved next_state.
    - decision.flags_cleared carries RECUT_REQUESTED (confirms the spec delta was loaded).

    Note: clearing RECUT_REQUESTED does not change the resolved state here because
    SP-007's ADVANCE_SAMPLE_PREP transition is not flag-dependent. This test's job is
    coverage + crash-guard; the logic-inversion case is unobservable with the current
    rule catalog (full guard comes with Phase B parity).
    """
    from samantha_server.api.routing import dispatch_event

    ctx = _make_sp007_ctx()
    rule_index = _make_rule_index()
    deps = _make_deps(rule_index=rule_index)
    written = deps.pop("_written")

    async def run() -> tuple[Any, Any, Any]:
        return await dispatch_event(
            ctx,
            session_id="gh328-sp007-clear-flags",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            **deps,
        )

    decision, dispatch_ctx, receipt = asyncio.run(run())

    assert dispatch_ctx.routing_path == "deterministic"
    assert decision.applied_rule_id == "SP-007", (
        f"Expected SP-007 to fire, got {decision.applied_rule_id!r}"
    )
    assert decision.next_state == "SAMPLE_PREP_QC", (
        f"Expected 'SAMPLE_PREP_QC' (ADVANCE_SAMPLE_PREP from SAMPLE_PREP_SECTIONING), "
        f"got {decision.next_state!r}"
    )
    assert "RECUT_REQUESTED" in decision.flags_cleared, (
        f"Expected RECUT_REQUESTED in decision.flags_cleared, got {decision.flags_cleared!r}"
    )
    assert len(written) == 1, f"Expected 1 receipt, got {len(written)}"
    assert written[0].decision.next_state == "SAMPLE_PREP_QC", (
        f"Receipt stamped wrong next_state: {written[0].decision.next_state!r}"
    )
