"""Tests for samantha_server.api.routing.dispatch_event — Slice 3."""

from __future__ import annotations

import asyncio
import pathlib
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

from samantha_server.engine.decision import EngineDecision
from samantha_server.llm.client import LLMResponse
from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.queue.priority import EventPriority
from samantha_server.receipts.signing import SignedReceipt
from tests.api.helpers import _CANNED_JSON_TEXT

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ctx(
    current_state: str = "ACCESSIONING",
    event_type: str = "clinical_query",
) -> SpecimenContext:
    return SpecimenContext(
        order=Order(
            order_id="DISPATCH-001",
            patient_name=None,
            patient_sex="F",
            age=40,
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
        event=Event(event_type=event_type, event_data={"query": "test?"}, step_index=0),
    )


def _make_mock_llm(text: str = "Test response.") -> MagicMock:
    mock = MagicMock()
    mock.model_id = "test-model"
    mock.complete.return_value = LLMResponse(
        text=text,
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


def _make_minimal_deps() -> dict[str, Any]:
    """Build minimal dependency kwargs for dispatch_event."""
    from samantha_server.api.receipt_writer import ReceiptWriter
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.rules.loader import RuleIndex
    from samantha_server.skills.loader import discover

    tmp = pathlib.Path(tempfile.mkdtemp())
    rw = ReceiptWriter.open(tmp / "r.db")

    return {
        "receipt_writer": rw,
        "write_lock": asyncio.Lock(),
        "counters": CounterRegistry(),
        "llm_client": _make_mock_llm(),
        "skills_index": discover(),
        "rule_index": RuleIndex([]),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dispatch_event_clinical_query_returns_decision_and_context() -> None:
    """dispatch_event returns (EngineDecision, EventDispatchContext, SignedReceipt)."""
    from samantha_server.api.event_context import EventDispatchContext
    from samantha_server.api.routing import dispatch_event

    ctx = _make_ctx(event_type="clinical_query")
    deps = _make_minimal_deps()

    async def run() -> None:
        decision, dispatch_ctx, receipt = await dispatch_event(
            ctx,
            session_id="test-sess",
            priority=EventPriority.ROUTINE,
            queue_wait_us=100,
            **deps,
        )
        assert isinstance(decision, EngineDecision)
        assert isinstance(dispatch_ctx, EventDispatchContext)
        assert isinstance(receipt, SignedReceipt)

    asyncio.run(run())


def test_dispatch_event_clinical_query_routing_path_is_llm() -> None:
    """clinical_query event gets routing_path='llm'."""
    from samantha_server.api.routing import dispatch_event

    ctx = _make_ctx(event_type="clinical_query")
    deps = _make_minimal_deps()

    async def run() -> None:
        _, dispatch_ctx, _ = await dispatch_event(
            ctx,
            session_id="test-sess",
            priority=EventPriority.ROUTINE,
            queue_wait_us=200,
            **deps,
        )
        assert dispatch_ctx.routing_path == "llm"

    asyncio.run(run())


def test_dispatch_event_queue_wait_propagated() -> None:
    """queue_wait_us is propagated to the EventDispatchContext."""
    from samantha_server.api.routing import dispatch_event

    ctx = _make_ctx(event_type="clinical_query")
    deps = _make_minimal_deps()

    async def run() -> None:
        _, dispatch_ctx, _ = await dispatch_event(
            ctx,
            session_id="test-sess",
            priority=EventPriority.ROUTINE,
            queue_wait_us=12345,
            **deps,
        )
        assert dispatch_ctx.queue_wait_us == 12345

    asyncio.run(run())


def test_dispatch_event_session_id_stamped_on_decision() -> None:
    """session_id is stamped onto the returned EngineDecision."""
    from samantha_server.api.routing import dispatch_event

    ctx = _make_ctx(event_type="clinical_query")
    deps = _make_minimal_deps()

    async def run() -> None:
        decision, _, _ = await dispatch_event(
            ctx,
            session_id="my-session",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            **deps,
        )
        assert decision.session_id == "my-session"

    asyncio.run(run())


def test_dispatch_event_receipt_persisted() -> None:
    """dispatch_event calls emit_receipt exactly once."""
    from samantha_server.api import routing as routing_mod
    from samantha_server.api.routing import dispatch_event

    ctx = _make_ctx(event_type="clinical_query")
    deps = _make_minimal_deps()

    written: list[SignedReceipt] = []
    original_emit = routing_mod.emit_receipt

    async def spy_emit(decision: Any, session_id: Any, **kwargs: Any) -> SignedReceipt:
        result = await original_emit(decision, session_id, **kwargs)
        written.append(result)
        return result

    async def run() -> None:
        with patch.object(routing_mod, "emit_receipt", side_effect=spy_emit):
            await dispatch_event(
                ctx,
                session_id="sess",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

    asyncio.run(run())
    assert len(written) == 1


def test_dispatch_event_pending_llm_review_routing_path() -> None:
    """PENDING_LLM_REVIEW state gives routing_path='llm'."""
    from samantha_server.api.routing import dispatch_event

    ctx = _make_ctx(current_state="PENDING_LLM_REVIEW", event_type="order_received")
    deps = _make_minimal_deps()
    deps["llm_client"].complete.return_value = LLMResponse(
        text="accepted",
        input_tokens=5,
        output_tokens=2,
        model_id="test-model",
        latency_us=500,
    )

    async def run() -> None:
        _, dispatch_ctx, _ = await dispatch_event(
            ctx,
            session_id="sess",
            priority=EventPriority.STAT,
            queue_wait_us=50,
            **deps,
        )
        assert dispatch_ctx.routing_path == "llm"
        assert dispatch_ctx.priority == EventPriority.STAT

    asyncio.run(run())


def test_dispatch_event_preflight_failure_routes_to_llm() -> None:
    """Unknown canonical fixative triggers clarification (routing_path='llm')."""
    from samantha_server.api.routing import dispatch_event

    ctx = SpecimenContext(
        order=Order(
            order_id="DISPATCH-002",
            patient_name=None,
            patient_sex="F",
            age=40,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="ethanol",  # not in canonical pick list
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )
    deps = _make_minimal_deps()
    deps["llm_client"].complete.return_value = LLMResponse(
        text="fixative=formalin",
        input_tokens=10,
        output_tokens=3,
        model_id="test-model",
        latency_us=800,
    )

    async def run() -> None:
        decision, dispatch_ctx, receipt = await dispatch_event(
            ctx,
            session_id="sess2",
            priority=EventPriority.ROUTINE,
            queue_wait_us=10,
            **deps,
        )
        assert dispatch_ctx.routing_path == "llm"
        assert decision.outcome == "needs_clarification"
        assert receipt is not None

    asyncio.run(run())


def test_dispatch_event_pending_llm_review_takes_priority_over_event_type() -> None:
    """PENDING_LLM_REVIEW state routes to handle_pending_llm_review even when
    event_type=='clinical_query'. Patches both handlers and emit_receipt;
    asserts the clinical branch is NOT called when the state-first guard fires.

    Regression for the deleted test_route_pending_llm_review_takes_priority_over_event_type
    's removed router-shim suite.
    """
    from unittest.mock import MagicMock, patch

    from samantha_server.api.routing import dispatch_event
    from samantha_server.engine.decision import EngineDecision

    ctx = _make_ctx(current_state="PENDING_LLM_REVIEW", event_type="clinical_query")
    deps = _make_minimal_deps()

    review_decision = MagicMock(spec=EngineDecision)
    review_decision.session_id = None
    review_decision.model_copy = MagicMock(return_value=review_decision)
    fake_receipt = MagicMock(spec=SignedReceipt)

    async def fake_emit(_decision: Any, _session_id: Any, **_kwargs: Any) -> SignedReceipt:
        return fake_receipt

    async def run() -> None:
        with (
            patch(
                "samantha_server.api.routing.handle_pending_llm_review",
                return_value=review_decision,
            ) as mock_review,
            patch("samantha_server.api.routing.handle_clinical_query") as mock_clinical,
            patch("samantha_server.api.routing.emit_receipt", side_effect=fake_emit),
        ):
            decision, _, _ = await dispatch_event(
                ctx,
                session_id="sess",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

        assert decision is review_decision
        mock_review.assert_called_once()
        mock_clinical.assert_not_called()

    asyncio.run(run())


def test_dispatch_event_forwards_preflight_result_to_handle_clarification() -> None:
    """The PreflightMissing instance is passed intact to handle_clarification.

    Asserts unknown_canonical_fields == ("fixative",) and missing_fields == ()
    flow through the dispatcher's preflight branch unchanged.

    Regression for the deleted test_route_forwards_preflight_result_to_handle_clarification.
    """
    from unittest.mock import MagicMock, patch

    from samantha_server.api.preflight import PreflightMissing
    from samantha_server.api.routing import dispatch_event
    from samantha_server.engine.decision import EngineDecision

    ctx = SpecimenContext(
        order=Order(
            order_id="DISPATCH-003",
            patient_name=None,
            patient_sex="F",
            age=40,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="ethanol",  # unknown canonical fixative
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )
    deps = _make_minimal_deps()

    clarif_decision = MagicMock(spec=EngineDecision)
    clarif_decision.session_id = None
    clarif_decision.model_copy = MagicMock(return_value=clarif_decision)
    fake_receipt = MagicMock(spec=SignedReceipt)

    async def fake_emit(_decision: Any, _session_id: Any, **_kwargs: Any) -> SignedReceipt:
        return fake_receipt

    async def run() -> None:
        with (
            patch(
                "samantha_server.api.routing.handle_clarification",
                return_value=clarif_decision,
            ) as mock_clarif,
            patch("samantha_server.api.routing.emit_receipt", side_effect=fake_emit),
        ):
            await dispatch_event(
                ctx,
                session_id="sess",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

        mock_clarif.assert_called_once()
        forwarded = mock_clarif.call_args.kwargs["preflight_result"]
        assert isinstance(forwarded, PreflightMissing)
        assert forwarded.unknown_canonical_fields == ("fixative",)
        assert forwarded.missing_fields == ()

    asyncio.run(run())


def test_dispatch_event_deterministic_order_received_emits_receipt() -> None:
    """order_received routes deterministically; emit_receipt is called exactly once.

    Previously: dispatch_event raised NotImplementedError for order_received.
    Now: the deterministic branch runs (empty RuleIndex → dispatch_empty) and
    emits a receipt via the shared emit_receipt chokepoint.

    Converted from test_dispatch_event_not_implemented_raises (the old refusal contract).
    """
    from unittest.mock import patch

    from samantha_server.api import routing as routing_mod
    from samantha_server.api.routing import dispatch_event

    ctx = _make_ctx(event_type="order_received")
    deps = _make_minimal_deps()

    written: list[SignedReceipt] = []
    original_emit = routing_mod.emit_receipt

    async def spy_emit(decision: Any, session_id: Any, **kwargs: Any) -> SignedReceipt:
        result = await original_emit(decision, session_id, **kwargs)
        written.append(result)
        return result

    async def run() -> None:
        with patch.object(routing_mod, "emit_receipt", side_effect=spy_emit):
            await dispatch_event(
                ctx,
                session_id="sess3",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

    asyncio.run(run())

    # Receipt-emission invariant: exactly one receipt via the shared chokepoint.
    assert len(written) == 1, "emit_receipt must be called exactly once on the deterministic arm"
    assert written[0].decision.outcome == "dispatch_empty"


def test_dispatch_event_deterministic_order_received_returns_dispatch_context() -> None:
    """order_received returns a complete (decision, EventDispatchContext, receipt) tuple.

    Previously: dispatch_event raised before constructing EventDispatchContext.
    Now: the deterministic branch returns normally with routing_path='deterministic'.

    Converted from test_dispatch_event_not_implemented_no_dispatch_context_created
    (the old refusal contract).
    """
    from samantha_server.api.event_context import EventDispatchContext
    from samantha_server.api.routing import dispatch_event

    ctx = _make_ctx(event_type="order_received")
    deps = _make_minimal_deps()

    async def run() -> tuple[Any, Any, Any]:
        return await dispatch_event(
            ctx,
            session_id="sess-deterministic",
            priority=EventPriority.ROUTINE,
            queue_wait_us=0,
            **deps,
        )

    decision, dispatch_ctx, receipt = asyncio.run(run())

    assert isinstance(dispatch_ctx, EventDispatchContext)
    assert dispatch_ctx.routing_path == "deterministic"
    assert decision.outcome == "dispatch_empty"
    assert receipt is not None


def test_dispatch_event_context_session_id_propagated() -> None:
    """session_id appears on EventDispatchContext."""
    from samantha_server.api.routing import dispatch_event

    ctx = _make_ctx(event_type="clinical_query")
    deps = _make_minimal_deps()

    async def run() -> None:
        _, dispatch_ctx, _ = await dispatch_event(
            ctx,
            session_id="ctx-session",
            priority=EventPriority.BACKGROUND,
            queue_wait_us=999,
            **deps,
        )
        assert dispatch_ctx.session_id == "ctx-session"
        assert dispatch_ctx.priority == EventPriority.BACKGROUND

    asyncio.run(run())
