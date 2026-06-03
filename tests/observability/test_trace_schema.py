"""Tests for the per-decision trace serializer (GH-125 Step 10).

The serializer is the durable contract behind dashboards (Step 11),
the drift alarm (Step 12), and the post-market monitoring story
(Step 14). Schema documented at ``docs/observability/trace-schema.md``.

Coverage:

- Schema-shape invariants for every Phase-2 outcome value (deterministic,
  clarification, hallucination refusal, LLM review, canonicalization).
- ``agreement`` enum: only ``deterministic_only`` and ``llm_only`` may be
  emitted by the v0 serializer (the ``both_*`` values are reserved for
  a future dual-route flow). Unknown routing_path fails soft with the
  ``unknown`` sentinel.
- PHI absence: sentinels are seeded into actual decision-trace fields
  (positive-control test) and asserted absent in the serialized trace.
- ``trace_id`` validation: malformed values raise at the boundary.
- Consistency check: routing_path=llm + non-refusal outcome with no
  model_id logs a warning.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from samantha_server.api.event_context import EventDispatchContext
from samantha_server.engine.decision import (
    CanonicalizationTrace,
    ClarificationTrace,
    EngineDecision,
    LLMReviewTrace,
    QueryTrace,
    RefusalTrace,
)
from samantha_server.queue.priority import EventPriority
from samantha_server.receipts.signing import SignedReceipt

# ---------------------------------------------------------------------------
# Helpers (defined first per file convention)
# ---------------------------------------------------------------------------


def _make_signed_receipt(decision: EngineDecision) -> SignedReceipt:
    # Imports are deferred because ``samantha_server.config`` reads
    # required-secret env vars at import time. Per-call import keeps
    # this module loadable even when the test conftest's sentinel
    # fixtures haven't activated yet (matters under collect-only).
    import samantha_server.config as cfg
    from samantha_server.receipts.signing import sign_decision

    return sign_decision(
        decision,
        key_id=cfg.RECEIPT_SIGNING_KEY_ID,
        signing_key=cfg.RECEIPT_SIGNING_KEY,
    )


def _dispatch_ctx(
    routing_path: str = "deterministic", queue_wait_us: int = 1234
) -> EventDispatchContext:
    return EventDispatchContext(
        session_id="trace-test-session",
        priority=EventPriority.ROUTINE,
        routing_path=routing_path,  # type: ignore[arg-type]
        queue_wait_us=queue_wait_us,
    )


def _flatten_strings(value: Any) -> str:
    """Concatenate every string value in a nested structure for sentinel scanning."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten_strings(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_strings(v) for v in value)
    return str(value)


def _deterministic_decision() -> EngineDecision:
    return EngineDecision(
        applied_rule_id="ACC-010",
        next_state="ACCEPTED",
        flags_added=(),
        flags_cleared=(),
        outcome="accepted",
        also_matched=(),
        dispatched_rule_ids=("ACC-010",),
        event_input_hash="a" * 64,
        primitive_traces={},
        latency_us=120,
    )


def _query_decision() -> EngineDecision:
    return EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="b" * 64,
        primitive_traces={},
        latency_us=4500,
        decision_traces=(
            QueryTrace(
                query_text_hash="h" * 64,
                skill_doc_hash="s" * 64,
                scenarios_cited=("qr-001",),
                model_id="test-model",
                response_text_hash="r" * 64,
            ),
        ),
    )


def _llm_review_decision() -> EngineDecision:
    return EngineDecision(
        applied_rule_id=None,
        next_state="ACCEPTED",
        flags_added=(),
        flags_cleared=("LLM_REVIEW_REQUESTED",),
        outcome="accepted_llm_review",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="c" * 64,
        primitive_traces={},
        latency_us=8000,
        decision_traces=(
            LLMReviewTrace(
                skill_doc_hash="s2" * 32,
                model_id="review-model",
                response_text_hash="r2" * 32,
                disposition="accept",
            ),
        ),
    )


def _clarification_decision(model_id: str = "clarify-model") -> EngineDecision:
    return EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="needs_clarification",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="d" * 64,
        primitive_traces={},
        latency_us=2000,
        decision_traces=(
            ClarificationTrace(
                missing_fields=("anatomic_site",),
                unknown_canonical_fields=(),
                suggested_values={"anatomic_site": "breast"},
                model_id=model_id,
            ),
        ),
    )


def _refusal_decision() -> EngineDecision:
    return EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="refused_phi_boundary",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="e" * 64,
        primitive_traces={},
        latency_us=0,
        decision_traces=(
            RefusalTrace(
                refusal_reason="STAGE_PRE_PHI_BOUNDARY",
                refusal_stage="PRE",
                judge_verdict=None,
                alternatives=(),
            ),
        ),
    )


def _canonicalization_decision() -> EngineDecision:
    """H5: a decision whose only decision_trace is a CanonicalizationTrace."""
    return EngineDecision(
        applied_rule_id="ACC-007",
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="canonicalized",
        also_matched=(),
        dispatched_rule_ids=("ACC-007",),
        event_input_hash="f" * 64,
        primitive_traces={},
        latency_us=80,
        decision_traces=(
            CanonicalizationTrace(
                field="anatomic_site",
                raw_value="left brest",
                canonical_value_attempted="breast",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Shape invariants
# ---------------------------------------------------------------------------


def test_to_trace_dict_includes_required_top_level_fields() -> None:
    """The serializer produces every field the schema doc lists."""
    from samantha_server.observability.trace import to_trace_dict

    decision = _deterministic_decision()
    receipt = _make_signed_receipt(decision)
    dispatch_ctx = _dispatch_ctx(routing_path="deterministic", queue_wait_us=999)

    trace = to_trace_dict(decision, dispatch_ctx, receipt, trace_id="0" * 32)

    assert trace["trace_id"] == "0" * 32
    assert trace["session_id"] == "trace-test-session"
    assert trace["routing_path"] == "deterministic"
    assert trace["agreement"] == "deterministic_only"
    assert trace["event_input_hash"] == "a" * 64
    assert trace["applied_rule_id"] == "ACC-010"
    assert trace["dispatched_rule_ids"] == ["ACC-010"]
    assert trace["also_matched"] == []
    assert trace["next_state"] == "ACCEPTED"
    assert trace["outcome"] == "accepted"
    assert isinstance(trace["latency_us"], dict)
    assert trace["latency_us"]["engine"] == 120
    assert trace["latency_us"]["llm_call"] is None  # no LLM on deterministic path
    assert trace["latency_us"]["queue_wait"] == 999
    assert trace["receipt_id"] == receipt.receipt_id
    assert trace["receipt_signature_key_id"] == receipt.signer_key_id
    assert trace["model_id"] is None
    assert trace["tool_calls"] == []
    assert trace["skill_doc_hash"] is None
    assert isinstance(trace["decision_traces"], list)


def test_to_trace_dict_query_carries_model_id_and_skill_doc_hash() -> None:
    """A query-routed decision exposes model_id + skill_doc_hash from QueryTrace."""
    from samantha_server.observability.trace import to_trace_dict

    decision = _query_decision()
    receipt = _make_signed_receipt(decision)
    dispatch_ctx = _dispatch_ctx(routing_path="llm")

    trace = to_trace_dict(decision, dispatch_ctx, receipt, trace_id="0" * 32)

    assert trace["routing_path"] == "llm"
    assert trace["agreement"] == "llm_only"
    assert trace["model_id"] == "test-model"
    assert trace["skill_doc_hash"] == "s" * 64
    # H2: latency_us.engine is None on LLM path; llm_call is populated.
    assert trace["latency_us"]["engine"] is None
    assert trace["latency_us"]["llm_call"] == decision.latency_us
    # M12: decision_traces[].kind asserted explicitly.
    assert trace["decision_traces"][0]["kind"] == "query"


def test_to_trace_dict_llm_review_carries_model_id() -> None:
    """LLM-review decisions expose model_id from LLMReviewTrace."""
    from samantha_server.observability.trace import to_trace_dict

    decision = _llm_review_decision()
    receipt = _make_signed_receipt(decision)
    dispatch_ctx = _dispatch_ctx(routing_path="llm")

    trace = to_trace_dict(decision, dispatch_ctx, receipt, trace_id="0" * 32)

    assert trace["model_id"] == "review-model"
    assert trace["agreement"] == "llm_only"
    assert trace["skill_doc_hash"] == "s2" * 32
    # H2: latency_us.engine is None on LLM path.
    assert trace["latency_us"]["engine"] is None
    assert trace["latency_us"]["llm_call"] == decision.latency_us
    assert trace["decision_traces"][0]["kind"] == "llm_review"


def test_to_trace_dict_clarification_outcome() -> None:
    """needs_clarification decisions are routed as llm_only."""
    from samantha_server.observability.trace import to_trace_dict

    decision = _clarification_decision()
    receipt = _make_signed_receipt(decision)
    dispatch_ctx = _dispatch_ctx(routing_path="llm")

    trace = to_trace_dict(decision, dispatch_ctx, receipt, trace_id="0" * 32)

    assert trace["outcome"] == "needs_clarification"
    assert trace["agreement"] == "llm_only"
    assert trace["model_id"] == "clarify-model"
    # M17: clarification has no skill_doc_hash field at all → null in trace.
    assert trace["skill_doc_hash"] is None
    assert trace["decision_traces"][0]["kind"] == "clarification"


def test_to_trace_dict_refusal_outcome() -> None:
    """Refusals carry the refusal kind in decision_traces; agreement reflects routing."""
    from samantha_server.observability.trace import to_trace_dict

    decision = _refusal_decision()
    receipt = _make_signed_receipt(decision)
    dispatch_ctx = _dispatch_ctx(routing_path="llm")

    trace = to_trace_dict(decision, dispatch_ctx, receipt, trace_id="0" * 32)

    assert trace["outcome"] == "refused_phi_boundary"
    assert trace["agreement"] == "llm_only"
    # Refusal traces don't expose model_id; serializer surfaces None rather
    # than raising. The consistency-check warning (M14) does NOT fire
    # because outcome.startswith("refused_").
    assert trace["model_id"] is None
    assert trace["decision_traces"][0]["kind"] == "refusal"


def test_to_trace_dict_canonicalization_path() -> None:
    """H5: a decision whose only trace is a CanonicalizationTrace serialises cleanly.

    Both ``model_id`` and ``skill_doc_hash`` are None — neither is
    declared on ``CanonicalizationTrace``. The ``kind`` discriminator
    is ``"canonicalization_warning"``.
    """
    from samantha_server.observability.trace import to_trace_dict

    decision = _canonicalization_decision()
    receipt = _make_signed_receipt(decision)
    dispatch_ctx = _dispatch_ctx(routing_path="deterministic")

    trace = to_trace_dict(decision, dispatch_ctx, receipt, trace_id="0" * 32)

    assert trace["model_id"] is None
    assert trace["skill_doc_hash"] is None
    assert trace["agreement"] == "deterministic_only"
    assert trace["decision_traces"][0]["kind"] == "canonicalization_warning"


# ---------------------------------------------------------------------------
# Agreement enum regression
# ---------------------------------------------------------------------------


def test_agreement_only_emits_deterministic_or_llm_only() -> None:
    """The serializer never emits ``both_*`` against any v0 input.

    GH-34's three-way disposition is a deterministic→LLM handoff, not a
    concurrent dual-route. ``both_agree`` / ``both_disagree`` are reserved
    values; emitting them in v0 would corrupt downstream dashboards.
    """
    from samantha_server.observability.trace import to_trace_dict

    cases = [
        (_deterministic_decision(), "deterministic"),
        (_query_decision(), "llm"),
        (_llm_review_decision(), "llm"),
        (_clarification_decision(), "llm"),
        (_refusal_decision(), "llm"),
        (_canonicalization_decision(), "deterministic"),
    ]
    for decision, routing in cases:
        trace = to_trace_dict(
            decision,
            _dispatch_ctx(routing_path=routing),
            _make_signed_receipt(decision),
            trace_id="0" * 32,
        )
        assert trace["agreement"] in ("deterministic_only", "llm_only"), (
            f"unexpected agreement value {trace['agreement']!r} for outcome={decision.outcome!r}"
        )


def test_agreement_unknown_routing_path_is_fail_soft() -> None:
    """M7: an unknown routing_path emits the ``unknown`` sentinel + warns; doesn't raise.

    Receipt is signed before the trace serializer runs; raising mid-
    serialization would lose the trace while keeping the receipt. Soft-
    fail keeps the trace alive with a visibly broken agreement value.
    """
    from samantha_server.observability.trace import to_trace_dict

    decision = _deterministic_decision()
    # Bypass the Literal type via the ``# type: ignore`` in _dispatch_ctx.
    dispatch_ctx = _dispatch_ctx(routing_path="mixed")

    trace = to_trace_dict(
        decision,
        dispatch_ctx,
        _make_signed_receipt(decision),
        trace_id="0" * 32,
    )
    assert trace["agreement"] == "unknown"


# ---------------------------------------------------------------------------
# trace_id validation (M15)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_trace_id",
    [
        "",  # empty
        "not-hex",  # non-hex chars
        "ABCDEF1234567890ABCDEF1234567890",  # uppercase
        "0" * 31,  # too short
        "0" * 33,  # too long
        "g" * 32,  # non-hex char
    ],
)
def test_to_trace_dict_rejects_malformed_trace_id(bad_trace_id: str) -> None:
    """M15: malformed trace_id raises at the boundary.

    Downstream Langfuse joins on trace_id would silently disconnect on
    a bad id; rejecting at the serializer boundary surfaces the bug
    where it is constructed.
    """
    from samantha_server.observability.trace import to_trace_dict

    decision = _deterministic_decision()
    with pytest.raises(ValueError, match="trace_id"):
        to_trace_dict(
            decision,
            _dispatch_ctx(),
            _make_signed_receipt(decision),
            trace_id=bad_trace_id,
        )


# ---------------------------------------------------------------------------
# M14: routing_path=llm + non-refusal + missing model_id consistency check
# ---------------------------------------------------------------------------


def test_consistency_warning_when_llm_path_outcome_lacks_model_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """M14: a non-refusal LLM-routed decision with no model_id logs a contract-gap warning.

    Constructed by emptying ``ClarificationTrace.model_id`` (its
    field default is ``""`` per Pydantic) on a non-refusal outcome.
    """
    from samantha_server.observability.trace import to_trace_dict

    decision = _clarification_decision(model_id="")  # forces model_id=None in trace
    dispatch_ctx = _dispatch_ctx(routing_path="llm")

    with caplog.at_level(logging.WARNING, logger="samantha_server.observability.trace"):
        trace = to_trace_dict(
            decision, dispatch_ctx, _make_signed_receipt(decision), trace_id="0" * 32
        )

    assert trace["model_id"] is None
    assert any("Trace contract gap" in rec.message for rec in caplog.records), (
        f"expected contract-gap warning; got logs={[r.message for r in caplog.records]!r}"
    )


def test_consistency_warning_silent_for_refusals(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The consistency check does NOT fire on refusal outcomes (refused_*)."""
    from samantha_server.observability.trace import to_trace_dict

    decision = _refusal_decision()
    dispatch_ctx = _dispatch_ctx(routing_path="llm")

    with caplog.at_level(logging.WARNING, logger="samantha_server.observability.trace"):
        to_trace_dict(decision, dispatch_ctx, _make_signed_receipt(decision), trace_id="0" * 32)

    assert not any("Trace contract gap" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# H3: PHI absence (positive-control test)
# ---------------------------------------------------------------------------


_PHI_SENTINEL_QUERY_HASH = "PHISENTINEL_QUERY_TEXT_HASH_NEVER_LEAK_THIS_VALUE_OK"
_PHI_SENTINEL_RAW_VALUE = "PHISENTINEL_RAW_VALUE_NEVER_LEAK_THIS_OK"
_PHI_SENTINEL_SUGGESTED = "PHISENTINEL_SUGGESTED_VALUE_NEVER_LEAK"


def test_phi_sentinels_seeded_into_decision_traces_do_not_surface() -> None:
    """H3: positive-control PHI test.

    Seeds known sentinel strings into actual decision_trace fields
    (the only places where caller-supplied strings can reach the
    serializer). The serializer's contract is that every emitted
    string is either a hex digest, an enum from a controlled
    vocabulary, or a structural id — caller-supplied free text must
    not surface.

    NOTE: ``decision_traces`` itself is serialised verbatim via
    ``model_dump()`` — the sentinels here will appear in the
    serialised ``decision_traces`` field, which is the intended
    pass-through. The test asserts that the *top-level* trace fields
    (which the dashboards filter on) don't carry the sentinels.
    """
    from samantha_server.observability.trace import to_trace_dict

    decision = EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="query_response",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="b" * 64,
        primitive_traces={},
        latency_us=4500,
        decision_traces=(
            QueryTrace(
                # Sentinel in a hash slot (would be a bug if a real PHI string
                # ended up here; this is the test's positive control).
                query_text_hash=_PHI_SENTINEL_QUERY_HASH,
                skill_doc_hash="s" * 64,
                scenarios_cited=("qr-001",),
                model_id="test-model",
                response_text_hash="r" * 64,
            ),
        ),
    )

    trace = to_trace_dict(
        decision,
        _dispatch_ctx(routing_path="llm"),
        _make_signed_receipt(decision),
        trace_id="0" * 32,
    )

    # Top-level fields the dashboards filter on: agreement, outcome,
    # event_input_hash, model_id, applied_rule_id, next_state. None of
    # these should ever carry PHI; check they don't carry our sentinels.
    top_level_string_values = {
        k: v
        for k, v in trace.items()
        if isinstance(v, str)
        and k not in ("decision_traces",)  # decision_traces is verbatim pass-through
    }
    flat = " ".join(top_level_string_values.values())

    assert _PHI_SENTINEL_QUERY_HASH not in flat, (
        "PHI sentinel from decision_trace surfaced in a top-level trace field; "
        "the serializer should only echo hashes/enums/structural ids at the top level."
    )
    assert _PHI_SENTINEL_RAW_VALUE not in flat
    assert _PHI_SENTINEL_SUGGESTED not in flat
