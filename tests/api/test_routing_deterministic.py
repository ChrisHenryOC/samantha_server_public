"""Tests for deterministic dispatch chokepoint in dispatch_event.

Slices 1-3 drive the new deterministic branch; Slice 4 converts the two
existing NotImplementedError-contract tests in test_routing.py to the new
deterministic behavior.

All tests use a real RuleIndex loaded from samantha_server/rules/specs/ so
that actual rules fire — no mocking of the engine internals.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.queue.priority import EventPriority
from samantha_server.receipts.signing import SignedReceipt

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SPECS_DIR = Path(__file__).resolve().parents[2] / "samantha_server" / "rules" / "specs"


def _make_rule_index() -> Any:
    """Return a real RuleIndex loaded from the project specs directory."""
    from samantha_server.rules.loader import RuleIndex, load_rule_specs

    return RuleIndex(load_rule_specs(_SPECS_DIR))


def _make_receipt_writer() -> Any:
    from samantha_server.api.receipt_writer import ReceiptWriter
    from samantha_server.receipts.store import _SCHEMA_SQL

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    return ReceiptWriter(conn)


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


def _make_deps(*, rule_index: Any | None = None) -> dict[str, Any]:
    """Build dispatch_event keyword deps with a real or empty RuleIndex."""
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.rules.loader import RuleIndex
    from samantha_server.skills.loader import discover

    return {
        "receipt_writer": _make_receipt_writer(),
        "write_lock": asyncio.Lock(),
        "counters": CounterRegistry(),
        "llm_client": _make_mock_llm(),
        "skills_index": discover(),
        "rule_index": rule_index if rule_index is not None else RuleIndex([]),
    }


def _make_order_received_ctx(
    *,
    patient_name: str | None = None,
    order_id: str = "GH324-001",
) -> SpecimenContext:
    """Return an ACCESSIONING ctx with event_type='order_received'.

    patient_name=None triggers ACC-001 (HOLD: missing patient name).
    With a real patient name and clean canonical fields, ACC-008 fires (ACCEPT).
    """
    return SpecimenContext(
        order=Order(
            order_id=order_id,
            patient_name=patient_name,
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
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )


# ---------------------------------------------------------------------------
# Slice 1: deterministic event returns a rule decision
# ---------------------------------------------------------------------------


def test_deterministic_event_returns_rule_decision() -> None:
    """order_received with real rules → EngineDecision with applied_rule_id.

    Pre-fix: dispatch_event raises NotImplementedError for order_received.
    Post-fix: the deterministic branch runs, ACC-001 fires (patient_name=None → HOLD).
    """
    from samantha_server.api.routing import dispatch_event
    from samantha_server.engine.decision import EngineDecision

    ctx = _make_order_received_ctx(patient_name=None)
    deps = _make_deps(rule_index=_make_rule_index())

    async def run() -> tuple[EngineDecision, Any, Any]:
        return await dispatch_event(
            ctx,
            session_id="gh324-slice1",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            **deps,
        )

    decision, dispatch_ctx, receipt = asyncio.run(run())

    assert isinstance(decision, EngineDecision)
    assert decision.applied_rule_id == "ACC-001"
    assert dispatch_ctx.routing_path == "deterministic"


# ---------------------------------------------------------------------------
# Slice 2: deterministic decision emits a signed receipt
# ---------------------------------------------------------------------------


def test_deterministic_event_emits_signed_receipt() -> None:
    """dispatch_event emits exactly one SignedReceipt for a deterministic event.

    The spy receipt_writer must receive one SignedReceipt whose decision carries
    ACC-001 as the applied_rule_id and a non-empty primitive_traces dict.
    """
    from samantha_server.api.routing import dispatch_event
    from tests.api.helpers import SpyReceiptWriter

    ctx = _make_order_received_ctx(patient_name=None)
    written: list[SignedReceipt] = []

    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.skills.loader import discover

    deps = {
        "receipt_writer": SpyReceiptWriter(written),
        "write_lock": asyncio.Lock(),
        "counters": CounterRegistry(),
        "llm_client": _make_mock_llm(),
        "skills_index": discover(),
        "rule_index": _make_rule_index(),
    }

    async def run() -> None:
        await dispatch_event(
            ctx,
            session_id="gh324-slice2",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            **deps,
        )

    asyncio.run(run())

    assert len(written) == 1, f"Expected 1 receipt, got {len(written)}"
    receipt = written[0]
    assert isinstance(receipt, SignedReceipt)
    assert receipt.decision.applied_rule_id == "ACC-001"
    assert receipt.decision.primitive_traces, "primitive_traces must be non-empty for a rule match"


# ---------------------------------------------------------------------------
# Slice 3: no-rule-match → dispatch_empty + receipt, no raise
# ---------------------------------------------------------------------------


def test_no_rule_match_returns_dispatch_empty_and_receipt() -> None:
    """Empty RuleIndex → dispatch_empty decision + receipt; no raise.

    Pre-fix: any order_received dispatched to the else-arm raised NotImplementedError.
    Post-fix: empty dispatch → outcome='dispatch_empty', applied_rule_id=None, receipt emitted.
    """
    from samantha_server.api.routing import dispatch_event
    from samantha_server.rules.loader import RuleIndex
    from tests.api.helpers import SpyReceiptWriter

    ctx = _make_order_received_ctx(patient_name=None, order_id="GH324-EMPTY")
    written: list[SignedReceipt] = []

    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.skills.loader import discover

    deps = {
        "receipt_writer": SpyReceiptWriter(written),
        "write_lock": asyncio.Lock(),
        "counters": CounterRegistry(),
        "llm_client": _make_mock_llm(),
        "skills_index": discover(),
        "rule_index": RuleIndex([]),  # empty index → no rule matches
    }

    async def run() -> Any:
        return await dispatch_event(
            ctx,
            session_id="gh324-slice3",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            **deps,
        )

    # Must NOT raise — this is the key behavioral change.
    decision, dispatch_ctx, receipt = asyncio.run(run())

    assert decision.applied_rule_id is None
    assert decision.outcome == "dispatch_empty"
    assert dispatch_ctx.routing_path == "deterministic"
    assert len(written) == 1, "dispatch_empty path must emit exactly one receipt"
    assert written[0].decision.outcome == "dispatch_empty"


# ---------------------------------------------------------------------------
# FIX 1: Symbolic transition resolution in the deterministic branch
# ---------------------------------------------------------------------------


def _make_grossing_complete_ctx(
    *,
    current_state: str = "ACCEPTED",
    order_id: str = "GH325-SP",
) -> SpecimenContext:
    """Return a ctx whose SP-001 rule fires (grossing_complete with outcome=success).

    With current_state='ACCEPTED' and event_type='grossing_complete',
    SP-001 fires and returns next_state='ADVANCE_SAMPLE_PREP'.
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
        current_state=current_state,
        flags=frozenset(),
        event=Event(
            event_type="grossing_complete",
            event_data={"outcome": "success"},
            step_index=0,
        ),
    )


def _make_he_staining_ctx(
    *,
    order_id: str = "GH325-HE",
) -> SpecimenContext:
    """Return a ctx for HE_STAINING + he_staining_complete (no rule fires → passthrough)."""
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
        current_state="HE_STAINING",
        flags=frozenset(),
        event=Event(
            event_type="he_staining_complete",
            event_data={},
            step_index=0,
        ),
    )


def test_symbolic_advance_sample_prep_resolves_to_concrete_state() -> None:
    """SP-001 fires ADVANCE_SAMPLE_PREP; deterministic branch must resolve
    to a concrete next_state before signing the receipt.

    Pre-fix: decision.next_state='ADVANCE_SAMPLE_PREP' (raw symbolic token) is stamped on
    the receipt — invalid. Post-fix: 'SAMPLE_PREP_PROCESSING' is returned and signed.
    """
    from samantha_server.api.routing import dispatch_event
    from tests.api.helpers import SpyReceiptWriter

    ctx = _make_grossing_complete_ctx(current_state="ACCEPTED")
    written: list[SignedReceipt] = []

    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.skills.loader import discover

    deps = {
        "receipt_writer": SpyReceiptWriter(written),
        "write_lock": asyncio.Lock(),
        "counters": CounterRegistry(),
        "llm_client": _make_mock_llm(),
        "skills_index": discover(),
        "rule_index": _make_rule_index(),
    }

    async def run() -> tuple[Any, Any, Any]:
        return await dispatch_event(
            ctx,
            session_id="gh325-symbolic-advance",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            **deps,
        )

    decision, dispatch_ctx, receipt = asyncio.run(run())

    assert dispatch_ctx.routing_path == "deterministic"
    assert decision.applied_rule_id == "SP-001"
    # The symbolic token must NOT appear anywhere
    assert decision.next_state == "SAMPLE_PREP_PROCESSING", (
        f"Expected 'SAMPLE_PREP_PROCESSING', got {decision.next_state!r} — "
        "ADVANCE_SAMPLE_PREP was not resolved"
    )
    assert receipt.decision.next_state == "SAMPLE_PREP_PROCESSING", (
        f"Receipt stamped raw symbolic token: {receipt.decision.next_state!r}"
    )


def test_passthrough_he_staining_resolves_to_he_qc() -> None:
    """dispatch_empty for HE_STAINING+he_staining_complete resolves via
    passthrough table to HE_QC on both the decision and the signed receipt.
    """
    from samantha_server.api.routing import dispatch_event
    from tests.api.helpers import SpyReceiptWriter

    ctx = _make_he_staining_ctx()
    written: list[SignedReceipt] = []

    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.skills.loader import discover

    deps = {
        "receipt_writer": SpyReceiptWriter(written),
        "write_lock": asyncio.Lock(),
        "counters": CounterRegistry(),
        "llm_client": _make_mock_llm(),
        "skills_index": discover(),
        "rule_index": _make_rule_index(),
    }

    async def run() -> tuple[Any, Any, Any]:
        return await dispatch_event(
            ctx,
            session_id="gh325-passthrough-he",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            **deps,
        )

    decision, dispatch_ctx, receipt = asyncio.run(run())

    assert dispatch_ctx.routing_path == "deterministic"
    assert decision.applied_rule_id is None  # dispatch_empty
    assert decision.next_state == "HE_QC", (
        f"Expected 'HE_QC' from passthrough, got {decision.next_state!r}"
    )
    assert receipt.decision.next_state == "HE_QC", (
        f"Receipt stamped wrong next_state: {receipt.decision.next_state!r}"
    )


# ---------------------------------------------------------------------------
# FIX 5: UndispatchedRuleError propagates through dispatch_event
# ---------------------------------------------------------------------------


def test_undispatched_rule_error_propagates_from_deterministic_branch() -> None:
    """UndispatchedRuleError from evaluate() propagates out of dispatch_event.

    A cross-session session_id mismatch causes evaluate() to raise UndispatchedRuleError.
    The deterministic branch must not swallow it — it must propagate to the caller.
    """
    import pytest

    from samantha_server.api.routing import dispatch_event
    from samantha_server.engine.dispatcher import list_applicable_rules
    from samantha_server.engine.evaluator import UndispatchedRuleError
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.skills.loader import discover

    # A grossing_complete ctx so SP-001 fires (deterministic path).
    ctx = _make_grossing_complete_ctx(current_state="ACCEPTED", order_id="GH325-UNDISPATCHED")

    rule_index = _make_rule_index()

    # Mint a dispatch token for session-A, then call dispatch_event with session-B.
    # evaluate() will verify the HMAC and raise UndispatchedRuleError.
    from unittest.mock import patch

    dispatch_token = list_applicable_rules(ctx, rule_index, session_id="session-A", ttl_sec=60)

    def _patched_list_applicable_rules(c: Any, ri: Any, *, session_id: str, ttl_sec: int) -> Any:
        # Return the token minted for session-A regardless of session_id passed
        return dispatch_token

    async def run() -> None:
        with patch(
            "samantha_server.api.routing.list_applicable_rules",
            side_effect=_patched_list_applicable_rules,
        ):
            await dispatch_event(
                ctx,
                session_id="session-B",  # mismatches the token → UndispatchedRuleError
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                receipt_writer=_make_receipt_writer(),
                write_lock=asyncio.Lock(),
                counters=CounterRegistry(),
                llm_client=_make_mock_llm(),
                skills_index=discover(),
                rule_index=rule_index,
            )

    with pytest.raises(UndispatchedRuleError):
        asyncio.run(run())


# ---------------------------------------------------------------------------
# dispatch_empty unknown-event observability
# ---------------------------------------------------------------------------


def _make_unknown_event_ctx(
    *,
    event_type: str,
    current_state: str = "ACCESSIONING",
    order_id: str = "GH326-UNKNOWN",
) -> SpecimenContext:
    """Return a ctx with the given event_type. Accessioning state so the state is recognized."""
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
        current_state=current_state,
        flags=frozenset(),
        event=Event(event_type=event_type, event_data={}, step_index=0),
    )


def test_dispatch_empty_unknown_event_type_increments_counter_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """dispatch_empty with an unknown event_type bumps dispatch_unknown_event_type
    counter and emits a WARNING log.

    We use an ACCESSIONING state (so it's in STATE_TO_STEP) but a completely fabricated
    event_type that no rule covers and that is not in _PASSTHROUGH_TRANSITIONS. The engine
    returns dispatch_empty and the orchestrator must detect the unknown event_type.
    """
    from samantha_server.api.routing import dispatch_event
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.skills.loader import discover

    ctx = _make_unknown_event_ctx(
        event_type="totally_unknown_event_xyz", current_state="ACCESSIONING"
    )
    counters = CounterRegistry()

    async def run() -> Any:
        return await dispatch_event(
            ctx,
            session_id="gh326-unknown-event",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            receipt_writer=_make_receipt_writer(),
            write_lock=asyncio.Lock(),
            counters=counters,
            llm_client=_make_mock_llm(),
            skills_index=discover(),
            rule_index=_make_rule_index(),
        )

    with caplog.at_level(logging.WARNING, logger="samantha_server.api.routing"):
        asyncio.run(run())

    assert counters.dispatch_unknown_event_type.value == 1, (
        "dispatch_unknown_event_type counter must be incremented for unknown event_type"
    )
    assert any(
        "totally_unknown_event_xyz" in record.message and record.levelno == logging.WARNING
        for record in caplog.records
    ), "A WARNING log containing the unknown event_type must be emitted"


def test_dispatch_empty_known_event_no_rule_match_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """dispatch_empty with a known event_type that matches no rule must NOT
    increment the counter and must NOT emit a WARNING.

    'order_received' is a known event_type (real ACC rules cover it in the loaded index).
    We dispatch in HE_QC state where no rule fires for order_received — dispatch_empty,
    but the event_type IS known. This is a legitimate no-rule-match and must be silent.
    """
    from samantha_server.api.routing import dispatch_event
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.skills.loader import discover

    # HE_QC is in STATE_TO_STEP → HE_QC step; 'order_received' triggers ACC rules, not HE_QC
    # rules. So the dispatcher returns dispatch_empty for this (state, event_type) pair,
    # but 'order_received' is still in the known set (derived from ACC rules in the index).
    ctx = _make_unknown_event_ctx(
        event_type="order_received", current_state="HE_QC", order_id="GH326-KNOWN-NOMATCH"
    )
    counters = CounterRegistry()

    async def run() -> Any:
        return await dispatch_event(
            ctx,
            session_id="gh326-known-nomatch",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            receipt_writer=_make_receipt_writer(),
            write_lock=asyncio.Lock(),
            counters=counters,
            llm_client=_make_mock_llm(),
            skills_index=discover(),
            rule_index=_make_rule_index(),  # real index: order_received IS a known event_type
        )

    with caplog.at_level(logging.WARNING, logger="samantha_server.api.routing"):
        asyncio.run(run())

    assert counters.dispatch_unknown_event_type.value == 0, (
        "Known event_type with no rule match must NOT increment dispatch_unknown_event_type"
    )
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 0, (
        f"Known event_type with no rule match must NOT emit a WARNING; got: {warning_records}"
    )


# ---------------------------------------------------------------------------
# Joint-propagation pin
# ---------------------------------------------------------------------------


def test_deterministic_event_carries_both_next_state_and_session_id() -> None:
    """The merged model_copy contract — a DETERMINISTIC state-changing
    event must produce a decision and receipt that carry BOTH the resolved next_state
    AND the caller's session_id.

    SP-001 fires on grossing_complete in ACCEPTED state and transitions to
    SAMPLE_PREP_PROCESSING (resolved from ADVANCE_SAMPLE_PREP). This exercises
    the resolved_state branch of the joint model_copy call in routing.py, pinning
    that neither field is lost when both are stamped in a single model_copy update.
    """
    from samantha_server.api.routing import dispatch_event
    from tests.api.helpers import SpyReceiptWriter

    ctx = _make_grossing_complete_ctx(current_state="ACCEPTED", order_id="PR402-FIX2")
    written: list[SignedReceipt] = []

    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.skills.loader import discover

    deps = {
        "receipt_writer": SpyReceiptWriter(written),
        "write_lock": asyncio.Lock(),
        "counters": CounterRegistry(),
        "llm_client": _make_mock_llm(),
        "skills_index": discover(),
        "rule_index": _make_rule_index(),
    }

    async def run() -> tuple[Any, Any, Any]:
        return await dispatch_event(
            ctx,
            session_id="pr402-fix2-session",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            **deps,
        )

    decision, dispatch_ctx, receipt = asyncio.run(run())

    # Both fields must be present and correct on the returned decision.
    assert decision.next_state == "SAMPLE_PREP_PROCESSING", (
        f"Expected resolved next_state 'SAMPLE_PREP_PROCESSING', got {decision.next_state!r}"
    )
    assert decision.session_id == "pr402-fix2-session", (
        f"Expected session_id 'pr402-fix2-session' on decision, got {decision.session_id!r}"
    )

    # The receipt must carry the same values (the receipt is signed over stamped decision).
    assert receipt.decision.next_state == "SAMPLE_PREP_PROCESSING", (
        f"Receipt stamped wrong next_state: {receipt.decision.next_state!r}"
    )
    assert receipt.decision.session_id == "pr402-fix2-session", (
        f"Receipt stamped wrong session_id: {receipt.decision.session_id!r}"
    )
    assert dispatch_ctx.routing_path == "deterministic"
