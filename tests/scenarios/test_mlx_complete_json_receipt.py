"""MLXClient.complete_json typed-error contract and receipt-emission invariant.

Before the fix, MLXClient.complete_json raised NotImplementedError (not in
LLMClientError hierarchy). Handlers caught only LLMClientError, so a
PENDING_LLM_REVIEW event with LLM_PROVIDER=mlx propagated the raw
NotImplementedError through dispatch_event, producing a 500 with no signed
receipt and violating the "every decision path emits a receipt" invariant.

The fix raises LLMInferenceError (a subclass of LLMClientError) from
complete_json so handlers except LLMClientError catches it and returns a
refused_llm_unavailable EngineDecision, which the router signs into a receipt.

Slices:
  Slice 2: PENDING_LLM_REVIEW dispatch with complete_json raising LLMInferenceError
           produces refused_llm_unavailable + underlying_error_type + signed receipt.
  Slice 3: clinical_query dispatch with complete_json raising LLMInferenceError
           produces refused_llm_unavailable + underlying_error_type + signed receipt.

additions (Slice 3):
- clinical_query dispatch with complete_json returning non-JSON text → receipt carries
  outcome='refused_unparseable_response' and RefusalTrace(STAGE_PRE_UNPARSEABLE).

Red-proof: both tests were verified to fail when mlx_client.py still raised
NotImplementedError (confirmed by temporarily reverting the Slice 1 production
change and re-running the suite).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.queue.priority import EventPriority
from samantha_server.receipts.signing import SignedReceipt

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_failing_json_llm_stub() -> MagicMock:
    """Stub LLMClient whose complete_json raises LLMInferenceError.

    Mirrors the error *type* contract that MLXClient.complete_json now
    enforces — the cause text is a deliberate test sentinel, not a
    copy of the production message (the handlers only dispatch on the
    exception type). Using a stub rather than the real MLXClient because
    constructing MLXClient requires mlx-lm to be installed, and the Slice 1
    unit test already pins the MLXClient behavior end-to-end.
    """
    from samantha_server.errors import LLMInferenceError

    mock = MagicMock()
    mock.model_id = "stub-mlx-model"
    mock.complete_json.side_effect = LLMInferenceError(
        model_id="stub-mlx-model",
        cause="MLXClient.complete_json is not supported (test stub).",
    )
    return mock


def _make_pending_llm_review_ctx(order_id: str = "GH376-LLM-001") -> SpecimenContext:
    """Return a PENDING_LLM_REVIEW SpecimenContext with order_received event."""
    return SpecimenContext(
        order=Order(
            order_id=order_id,
            patient_name=None,
            patient_sex="F",
            age=45,
            specimen_type="frozen_section",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state="PENDING_LLM_REVIEW",
        flags=frozenset({"LLM_REVIEW_REQUESTED"}),
        event=Event(event_type="order_received", event_data={}, step_index=1),
    )


def _make_clinical_query_ctx(order_id: str = "GH376-QR-001") -> SpecimenContext:
    """Return an ACCESSIONING SpecimenContext with clinical_query event."""
    return SpecimenContext(
        order=Order(
            order_id=order_id,
            patient_name=None,
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
        event=Event(
            event_type="clinical_query",
            event_data={"query": "Which orders are pending?"},
            step_index=0,
        ),
    )


def _make_deps(*, llm_client: Any) -> dict[str, Any]:
    from samantha_server.observability.counters import CounterRegistry
    from samantha_server.rules.loader import RuleIndex
    from samantha_server.skills.loader import discover
    from tests.api.helpers import SpyReceiptWriter

    written: list[SignedReceipt] = []
    return {
        "receipt_writer": SpyReceiptWriter(written),
        "write_lock": asyncio.Lock(),
        "counters": CounterRegistry(),
        "llm_client": llm_client,
        # Empty rule index per the make_dispatch_deps convention (conftest):
        # the LLM routing path under test never evaluates deterministic rules.
        # skills_index stays real — the route resolves the skill body before
        # complete_json (an empty index would refuse as skill-unavailable).
        "rule_index": RuleIndex([]),
        "skills_index": discover(),
        "_written": written,
    }


# ---------------------------------------------------------------------------
# Slice 2: PENDING_LLM_REVIEW dispatch — receipt-emission invariant
# ---------------------------------------------------------------------------


class TestSlice2PendingLlmReviewReceiptInvariant:
    """PENDING_LLM_REVIEW + complete_json raising LLMInferenceError.

    Before the fix, NotImplementedError escaped the handler and no receipt
    was emitted. After the fix, the handler catches LLMInferenceError and
    returns refused_llm_unavailable; the router emits a signed receipt.
    """

    def test_pending_llm_review_does_not_raise(self) -> None:
        """dispatch_event must not raise when complete_json raises LLMInferenceError."""
        from samantha_server.api.routing import dispatch_event

        ctx = _make_pending_llm_review_ctx()
        deps = _make_deps(llm_client=_make_failing_json_llm_stub())
        deps.pop("_written")

        async def run() -> tuple[Any, Any, Any]:
            return await dispatch_event(
                ctx,
                session_id="gh376-slice2-no-raise",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

        # Must not raise — this was the bug: NotImplementedError escaped.
        asyncio.run(run())

    def test_pending_llm_review_produces_refused_llm_unavailable(self) -> None:
        """dispatch_event must return outcome=refused_llm_unavailable."""
        from samantha_server.api.routing import dispatch_event

        ctx = _make_pending_llm_review_ctx()
        deps = _make_deps(llm_client=_make_failing_json_llm_stub())
        deps.pop("_written")

        async def run() -> Any:
            decision, _, _ = await dispatch_event(
                ctx,
                session_id="gh376-slice2-outcome",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )
            return decision

        decision = asyncio.run(run())
        assert decision.outcome == "refused_llm_unavailable", (
            f"Expected outcome='refused_llm_unavailable', got {decision.outcome!r}"
        )

    def test_pending_llm_review_underlying_error_type_is_llm_inference_error(self) -> None:
        """RefusalTrace.underlying_error_type must be 'LLMInferenceError'."""
        from samantha_server.api.routing import dispatch_event
        from samantha_server.engine.decision import RefusalTrace

        ctx = _make_pending_llm_review_ctx()
        deps = _make_deps(llm_client=_make_failing_json_llm_stub())
        deps.pop("_written")

        async def run() -> Any:
            decision, _, _ = await dispatch_event(
                ctx,
                session_id="gh376-slice2-errtype",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )
            return decision

        decision = asyncio.run(run())
        assert len(decision.decision_traces) == 1
        trace = decision.decision_traces[0]
        assert isinstance(trace, RefusalTrace)
        assert trace.underlying_error_type == "LLMInferenceError", (
            "Expected underlying_error_type='LLMInferenceError', "
            f"got {trace.underlying_error_type!r}"
        )

    def test_pending_llm_review_emits_signed_receipt(self) -> None:
        """dispatch_event must emit exactly one signed receipt (receipt-emission invariant).

        This was the critical invariant violated before the fix: no receipt was
        emitted when NotImplementedError escaped the handler.
        """
        from samantha_server.api.routing import dispatch_event

        ctx = _make_pending_llm_review_ctx()
        deps = _make_deps(llm_client=_make_failing_json_llm_stub())
        written = deps.pop("_written")

        async def run() -> None:
            await dispatch_event(
                ctx,
                session_id="gh376-slice2-receipt",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

        asyncio.run(run())

        assert len(written) == 1, (
            f"Expected exactly 1 signed receipt; got {len(written)}. "
            "Before the fix, NotImplementedError escaped the handler and no receipt was emitted."
        )
        receipt = written[0]
        assert isinstance(receipt, SignedReceipt)
        assert receipt.decision.outcome == "refused_llm_unavailable"

    def test_pending_llm_review_warning_log_names_the_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The WARNING at the catch site must carry str(exc).

        Without it, a permanent capability gap (mlx + specimen review) is
        indistinguishable in logs from a transient inference failure — the
        operator-actionable cause only surfaced at DEBUG or on the OTel span.
        """
        from samantha_server.api.routing import dispatch_event

        ctx = _make_pending_llm_review_ctx()
        deps = _make_deps(llm_client=_make_failing_json_llm_stub())
        deps.pop("_written")

        async def run() -> None:
            await dispatch_event(
                ctx,
                session_id="gh376-slice2-warnlog",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

        with caplog.at_level(logging.WARNING, logger="samantha_server.llm.handlers"):
            asyncio.run(run())

        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING and "complete_json" in record.getMessage()
        ]
        assert warnings, "expected a WARNING from the complete_json catch site"
        assert any("stub-mlx-model" in message for message in warnings), (
            "WARNING log must include str(exc) (model_id + cause) so the operator "
            f"can identify the failing client; got: {warnings!r}"
        )


# ---------------------------------------------------------------------------
# Slice 3: clinical_query dispatch — receipt-emission invariant
# ---------------------------------------------------------------------------


class TestSlice3ClinicalQueryReceiptInvariant:
    """clinical_query + complete_json raising LLMInferenceError.

    The clinical_query handler (_handle_clinical_query_json) also calls
    complete_json and catches only LLMClientError. Before the fix, an MLX
    client would have raised NotImplementedError here too (if the startup
    guard were bypassed). After the fix, the typed error flows through the
    same refused_llm_unavailable path with a signed receipt.
    """

    def test_clinical_query_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """dispatch_event must not raise when complete_json raises LLMInferenceError."""
        from samantha_server import config
        from samantha_server.api.routing import dispatch_event

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        ctx = _make_clinical_query_ctx()
        deps = _make_deps(llm_client=_make_failing_json_llm_stub())
        deps.pop("_written")

        async def run() -> tuple[Any, Any, Any]:
            return await dispatch_event(
                ctx,
                session_id="gh376-slice3-no-raise",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

        # Must not raise — this was the bug: NotImplementedError escaped.
        asyncio.run(run())

    def test_clinical_query_produces_refused_llm_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLMInferenceError from complete_json must produce refused_llm_unavailable."""
        from samantha_server import config
        from samantha_server.api.routing import dispatch_event

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        ctx = _make_clinical_query_ctx()
        deps = _make_deps(llm_client=_make_failing_json_llm_stub())
        deps.pop("_written")

        async def run() -> Any:
            decision, _, _ = await dispatch_event(
                ctx,
                session_id="gh376-slice3-outcome",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )
            return decision

        decision = asyncio.run(run())
        assert decision.outcome == "refused_llm_unavailable", (
            f"Expected outcome='refused_llm_unavailable', got {decision.outcome!r}"
        )

    def test_clinical_query_underlying_error_type_is_llm_inference_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RefusalTrace.underlying_error_type must be 'LLMInferenceError' for clinical_query."""
        from samantha_server import config
        from samantha_server.api.routing import dispatch_event
        from samantha_server.engine.decision import RefusalTrace

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        ctx = _make_clinical_query_ctx()
        deps = _make_deps(llm_client=_make_failing_json_llm_stub())
        deps.pop("_written")

        async def run() -> Any:
            decision, _, _ = await dispatch_event(
                ctx,
                session_id="gh376-slice3-errtype",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )
            return decision

        decision = asyncio.run(run())
        assert len(decision.decision_traces) == 1
        trace = decision.decision_traces[0]
        assert isinstance(trace, RefusalTrace)
        assert trace.underlying_error_type == "LLMInferenceError", (
            "Expected underlying_error_type='LLMInferenceError', "
            f"got {trace.underlying_error_type!r}"
        )

    def test_clinical_query_emits_signed_receipt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """dispatch_event must emit exactly one signed receipt for clinical_query refusal."""
        from samantha_server import config
        from samantha_server.api.routing import dispatch_event

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        ctx = _make_clinical_query_ctx()
        deps = _make_deps(llm_client=_make_failing_json_llm_stub())
        written = deps.pop("_written")

        async def run() -> None:
            await dispatch_event(
                ctx,
                session_id="gh376-slice3-receipt",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

        asyncio.run(run())

        assert len(written) == 1, f"Expected exactly 1 signed receipt; got {len(written)}."
        receipt = written[0]
        assert isinstance(receipt, SignedReceipt)
        assert receipt.decision.outcome == "refused_llm_unavailable"

    def test_clinical_query_warning_log_names_the_error(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The clinical-query catch site WARNING must carry str(exc)."""
        from samantha_server import config
        from samantha_server.api.routing import dispatch_event

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        ctx = _make_clinical_query_ctx()
        deps = _make_deps(llm_client=_make_failing_json_llm_stub())
        deps.pop("_written")

        async def run() -> None:
            await dispatch_event(
                ctx,
                session_id="gh376-slice3-warnlog",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

        with caplog.at_level(logging.WARNING, logger="samantha_server.llm.handlers"):
            asyncio.run(run())

        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING and "complete_json" in record.getMessage()
        ]
        assert warnings, "expected a WARNING from the complete_json catch site"
        assert any("stub-mlx-model" in message for message in warnings), (
            "WARNING log must include str(exc) (model_id + cause) so the operator "
            f"can identify the failing client; got: {warnings!r}"
        )


# ---------------------------------------------------------------------------
# Receipt-level test — parse-failure refusal outcome propagates
# ---------------------------------------------------------------------------


def _make_parse_failure_stub() -> MagicMock:
    """Stub LLMClient whose complete_json returns non-JSON text.

    Triggers the parse-failure branch in _handle_clinical_query_json, which
    fixed to return refused_unparseable_response instead of query_response.
    """
    from samantha_server.llm.client import LLMResponse

    mock = MagicMock()
    mock.model_id = "stub-model"
    mock.complete_json.return_value = LLMResponse(
        text="this is definitely not valid JSON",
        input_tokens=5,
        output_tokens=10,
        model_id="stub-model",
        latency_us=500,
    )
    return mock


class TestGH380ParseFailureReceiptInvariant:
    """clinical_query parse failure carries refusal outcome in receipt.

    Verifies the full dispatch path: complete_json returns non-JSON text →
    handler emits refused_unparseable_response → router signs receipt →
    receipt.decision.outcome == 'refused_unparseable_response'.
    """

    def test_parse_failure_receipt_carries_refused_outcome(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Receipt outcome must be 'refused_unparseable_response' on JSON parse failure."""
        from samantha_server import config
        from samantha_server.api.routing import dispatch_event

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        ctx = _make_clinical_query_ctx()
        deps = _make_deps(llm_client=_make_parse_failure_stub())
        written = deps.pop("_written")

        async def run() -> None:
            await dispatch_event(
                ctx,
                session_id="gh380-parse-fail-receipt",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

        asyncio.run(run())

        assert len(written) == 1, f"Expected exactly 1 signed receipt; got {len(written)}."
        receipt = written[0]
        assert isinstance(receipt, SignedReceipt)
        assert receipt.decision.outcome == "refused_unparseable_response", (
            f"Parse failure must produce refused_unparseable_response in receipt; "
            f"got {receipt.decision.outcome!r}"
        )

    def test_parse_failure_receipt_contains_refusal_trace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Receipt decision_traces must include RefusalTrace(STAGE_PRE_UNPARSEABLE)."""
        from samantha_server import config
        from samantha_server.api.routing import dispatch_event
        from samantha_server.engine.decision import RefusalTrace

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        ctx = _make_clinical_query_ctx()
        deps = _make_deps(llm_client=_make_parse_failure_stub())
        written = deps.pop("_written")

        async def run() -> None:
            await dispatch_event(
                ctx,
                session_id="gh380-parse-fail-trace",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

        asyncio.run(run())

        assert len(written) == 1
        traces = written[0].decision.decision_traces
        assert len(traces) == 1
        trace = traces[0]
        assert isinstance(trace, RefusalTrace), (
            f"Parse failure receipt must carry RefusalTrace, got {type(trace).__name__}"
        )
        assert trace.refusal_reason == "STAGE_PRE_UNPARSEABLE"


# ---------------------------------------------------------------------------
# Positive-path dispatch guard
# ---------------------------------------------------------------------------


def _make_specimen_review_success_stub(disposition: str) -> MagicMock:
    """Stub LLMClient whose complete_json returns a well-formed SpecimenReviewResponseV1.

    Returns a minimal valid payload for the given disposition ("accepted",
    "rejected", or "escalated"). Mirrors the pattern in test_llm_unavailable.py
    (test_query_with_llm_success_still_passes).
    """
    from samantha_server.llm.client import LLMResponse

    # json.dumps (not an f-string) so a quote/backslash in the
    # value can never silently malform the payload — matches the sibling stub.
    payload = json.dumps({"disposition": disposition, "reasoning": "test ok"})
    mock = MagicMock()
    mock.model_id = "stub-success-model"
    mock.complete_json.return_value = LLMResponse(
        text=payload,
        input_tokens=10,
        output_tokens=8,
        model_id="stub-success-model",
        latency_us=500,
    )
    return mock


def _make_clinical_query_success_stub(order_id: str = "ORD-GH385-001") -> MagicMock:
    """Stub LLMClient whose complete_json returns a well-formed QueryResponseV1.

    The returned order_ids list contains a single known ID so callers can assert
    the outcome without needing a full corpus fixture.
    """
    from samantha_server.llm.client import LLMResponse

    payload = json.dumps(
        {"answer_type": "order_list", "order_ids": [order_id], "reasoning": "test", "caveats": ""}
    )
    mock = MagicMock()
    mock.model_id = "stub-success-model"
    mock.complete_json.return_value = LLMResponse(
        text=payload,
        input_tokens=10,
        output_tokens=12,
        model_id="stub-success-model",
        latency_us=500,
    )
    return mock


class TestGH385PositivePathDispatch:
    """Positive-path dispatch guards for specimen review and clinical query.

    Mirrors test_query_with_llm_success_still_passes from test_llm_unavailable.py:
    a healthy stub complete_json returning well-formed JSON must produce the success
    outcome (not a refusal). Regression guard: if a future refactor accidentally
    broadens the refusal-detection or error-catch logic, a healthy response should
    not flip to refused_*.
    """

    def test_pending_llm_review_accepted_stub_produces_accepted_llm_review(
        self,
    ) -> None:
        """PENDING_LLM_REVIEW + accepted disposition stub → outcome=accepted_llm_review."""
        from samantha_server.api.routing import dispatch_event

        ctx = _make_pending_llm_review_ctx("GH385-POS-001")
        deps = _make_deps(llm_client=_make_specimen_review_success_stub("accepted"))
        deps.pop("_written")

        async def run() -> Any:
            decision, _, _ = await dispatch_event(
                ctx,
                session_id="gh385-pos-accepted",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )
            return decision

        decision = asyncio.run(run())
        assert decision.outcome == "accepted_llm_review", (
            f"Expected outcome='accepted_llm_review' for accepted disposition stub; "
            f"got {decision.outcome!r}"
        )
        assert decision.next_state == "ACCEPTED", (
            f"Expected next_state='ACCEPTED'; got {decision.next_state!r}"
        )

    def test_pending_llm_review_success_emits_signed_receipt(
        self,
    ) -> None:
        """PENDING_LLM_REVIEW success path must emit exactly one signed receipt."""
        from samantha_server.api.routing import dispatch_event

        ctx = _make_pending_llm_review_ctx("GH385-POS-002")
        deps = _make_deps(llm_client=_make_specimen_review_success_stub("accepted"))
        written = deps.pop("_written")

        async def run() -> None:
            await dispatch_event(
                ctx,
                session_id="gh385-pos-receipt",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

        asyncio.run(run())

        assert len(written) == 1, (
            f"Expected exactly 1 signed receipt on success path; got {len(written)}"
        )
        receipt = written[0]
        assert isinstance(receipt, SignedReceipt)
        assert receipt.decision.outcome == "accepted_llm_review"

    def test_clinical_query_success_stub_produces_query_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """clinical_query + well-formed QueryResponseV1 → outcome='query_response'."""
        from samantha_server import config
        from samantha_server.api.routing import dispatch_event

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        ctx = _make_clinical_query_ctx("GH385-QR-POS-001")
        deps = _make_deps(llm_client=_make_clinical_query_success_stub())
        deps.pop("_written")

        async def run() -> Any:
            decision, _, _ = await dispatch_event(
                ctx,
                session_id="gh385-qr-pos",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )
            return decision

        decision = asyncio.run(run())
        assert decision.outcome == "query_response", (
            f"Expected outcome='query_response' for a healthy clinical_query stub; "
            f"got {decision.outcome!r}"
        )

    def test_clinical_query_success_emits_signed_receipt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """clinical_query success path must emit exactly one signed receipt."""
        from samantha_server import config
        from samantha_server.api.routing import dispatch_event

        monkeypatch.setattr(config, "SAMANTHA_LLM_OUTPUT_MODE", "json")

        ctx = _make_clinical_query_ctx("GH385-QR-POS-002")
        deps = _make_deps(llm_client=_make_clinical_query_success_stub())
        written = deps.pop("_written")

        async def run() -> None:
            await dispatch_event(
                ctx,
                session_id="gh385-qr-pos-receipt",
                priority=EventPriority.ROUTINE,
                queue_wait_us=0,
                **deps,
            )

        asyncio.run(run())

        assert len(written) == 1, (
            f"Expected exactly 1 signed receipt on clinical_query success; got {len(written)}"
        )
        receipt = written[0]
        assert isinstance(receipt, SignedReceipt)
        assert receipt.decision.outcome == "query_response"
