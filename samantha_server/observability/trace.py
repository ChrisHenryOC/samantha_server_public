"""Per-decision trace schema serializer (GH-125 Step 10).

``to_trace_dict`` converts an ``EngineDecision`` plus its dispatch
context and signed receipt into the documented Langfuse trace
shape. The schema is the durable contract behind:

- the dashboards (Step 11 / GH-126),
- the drift alarm (Step 12 / GH-127),
- the eval-harness replay tags (Step 13 / GH-128), and
- the post-market-monitoring story (Step 14 / GH-129).

Schema documentation: ``docs/observability/trace-schema.md``.

PHI boundary: every value the serializer emits is either a hex digest,
an enum from a controlled vocabulary, a numeric latency, or a
structural identifier (rule id, receipt id). Patient-bearing fields
never appear — the test ``tests/observability/test_trace_schema.py``
pins the contract.

Decision-path purity: this module lives under ``observability/`` and
consumes ``EngineDecision`` / ``SignedReceipt`` / ``EventDispatchContext``.
It must not import from the deterministic engine, rules, or
primitives modules.
"""

from __future__ import annotations

import logging
import re
from typing import Literal, TypedDict

from samantha_server.api.event_context import EventDispatchContext
from samantha_server.engine.decision import (
    ClarificationTrace,
    EngineDecision,
    LLMReviewTrace,
    QueryTrace,
)
from samantha_server.receipts.signing import SignedReceipt

_log = logging.getLogger(__name__)

# OTel trace IDs are 128 bits rendered as 32 lowercase hex characters.
# The serializer rejects malformed values at the boundary so downstream
# Langfuse joins on ``trace_id`` don't silently disconnect.
_TRACE_ID_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{32}$")


# ---------------------------------------------------------------------------
# Schema TypedDicts (mirror docs/observability/trace-schema.md exactly)
# ---------------------------------------------------------------------------


class LatencyDict(TypedDict):
    """The ``latency_us`` sub-shape — three fields, each microsecond integer.

    ``engine`` is null on the LLM path; ``llm_call`` is null on the
    deterministic path. ``queue_wait`` is always populated.
    """

    engine: int | None
    llm_call: int | None
    queue_wait: int


class ToolCallDict(TypedDict):
    """One entry in the ``tool_calls`` array.

    Currently never populated — the v0 LLM path doesn't thread tool
    invocations through ``EngineDecision``. The shape is reserved so
    Phase-4 tool-catalog work has a stable contract.
    """

    name: str
    latency_us: int
    succeeded: bool


# Agreement enum values. ``both_agree`` and ``both_disagree`` are
# reserved for a future dual-route flow and never emitted in v0.
AgreementValue = Literal["deterministic_only", "llm_only", "both_agree", "both_disagree", "unknown"]


class TraceDict(TypedDict):
    """The Langfuse trace shape, mirrored 1:1 with trace-schema.md.

    Every consumer (dashboards, drift alarm, replay, monitoring story)
    reads from this exact key set. Adding a field requires the schema
    doc, this TypedDict, and the regression tests to be updated in
    the same PR.
    """

    trace_id: str
    session_id: str
    routing_path: str
    agreement: AgreementValue
    event_input_hash: str
    applied_rule_id: str | None
    dispatched_rule_ids: list[str]
    also_matched: list[str]
    next_state: str
    outcome: str
    latency_us: LatencyDict
    receipt_id: str
    receipt_signature_key_id: str
    model_id: str | None
    tool_calls: list[ToolCallDict]
    decision_traces: list[dict[str, object]]
    skill_doc_hash: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agreement_for(routing_path: Literal["deterministic", "llm"]) -> AgreementValue:
    """Map ``EventDispatchContext.routing_path`` to the schema's agreement enum.

    v0 emits only ``deterministic_only`` or ``llm_only``. ``both_agree``
    and ``both_disagree`` are reserved for a future dual-route flow
    (GH-34's three-way disposition is a deterministic→LLM handoff, not
    concurrent dual-routing). The regression test asserts the
    serializer never emits the reserved values.

    Fail-soft: an unknown value (e.g., a Phase-4 routing_path the
    serializer hasn't been updated for) is logged and mapped to the
    sentinel ``"unknown"``. Raising mid-serialization would lose the
    trace entirely while keeping the receipt — the receipt is the
    contract, the trace is observability; observability fails soft.
    """
    if routing_path == "deterministic":
        return "deterministic_only"
    if routing_path == "llm":
        return "llm_only"
    _log.warning(
        "Unknown routing_path %r in trace serializer; emitting agreement='unknown'. "
        "If this is a Phase-4 dual-route value, update _agreement_for + the schema "
        "doc + the regression test in the same PR.",
        routing_path,
    )
    return "unknown"


def to_trace_dict(
    decision: EngineDecision,
    dispatch_ctx: EventDispatchContext,
    receipt: SignedReceipt,
    *,
    trace_id: str,
) -> TraceDict:
    """Serialize a decision + dispatch context + signed receipt to the trace shape.

    Inputs are the three durable artifacts of a single dispatched event:
    ``decision`` (engine output), ``dispatch_ctx`` (orchestrator-owned
    fields), ``receipt`` (signed contract). ``trace_id`` is the OTel
    parent-span trace id rendered as a 32-char lowercase hex string —
    callers read it from ``span.get_span_context().trace_id`` and
    format it.

    The output dict matches ``docs/observability/trace-schema.md`` exactly.

    Raises ``ValueError`` only on malformed ``trace_id`` — that's a
    caller bug (downstream Langfuse joins would silently disconnect on
    a bad id, so we fail at the boundary instead).

    ``latency_us.engine`` is ``None`` on the LLM path because
    ``decision.latency_us`` carries the LLM-call wall time, not the
    deterministic engine wall time, on that path. ``latency_us.llm_call``
    is ``None`` on the deterministic path. Phase-4 dual-routing will
    populate both fields independently.

    ``tool_calls`` is currently always empty — the v0 LLM path does not
    yet thread tool invocations through ``EngineDecision``. The
    field's TypedDict shape is reserved for Phase-4 tool-catalog work.

    Consistency check: when ``routing_path == "llm"`` and the outcome
    is non-refusal, ``model_id`` should be non-None. If the
    invariant is violated (a late-stage refusal that left no
    decision_trace, for example), we log a warning so the gap is
    visible without crashing the serializer.
    """
    if not _TRACE_ID_RE.match(trace_id):
        raise ValueError(
            f"trace_id must be 32 lowercase hex characters; got len={len(trace_id)} "
            "(value omitted for log hygiene). Read it from "
            "span.get_span_context().trace_id formatted via format(..., '032x')."
        )

    # Inline the model_id / skill_doc_hash extraction (Beck rule 4 —
    # single-call-site helpers don't earn extraction).
    model_id: str | None = None
    skill_doc_hash: str | None = None
    for trace in decision.decision_traces:
        # Empty-string model_id from ClarificationTrace's default means
        # "no LLM call made"; surface as null in the trace.
        if (
            isinstance(trace, (QueryTrace, ClarificationTrace, LLMReviewTrace))
            and trace.model_id
            and model_id is None
        ):
            model_id = trace.model_id
        if isinstance(trace, (QueryTrace, LLMReviewTrace)) and skill_doc_hash is None:
            skill_doc_hash = trace.skill_doc_hash

    # Consistency check (M14): an LLM-routed non-refusal decision should
    # carry a model_id. A null here is a contract gap — log so the gap
    # is visible without losing the trace.
    if (
        dispatch_ctx.routing_path == "llm"
        and not decision.outcome.startswith("refused_")
        and model_id is None
    ):
        _log.warning(
            "Trace contract gap: routing_path='llm' and outcome=%r is non-refusal, "
            "but model_id is None. Decision_traces did not surface a model_id; "
            "check the LLM handler's decision_trace construction.",
            decision.outcome,
        )

    decision_traces_serialised = [t.model_dump() for t in decision.decision_traces]

    is_llm_path = dispatch_ctx.routing_path == "llm"

    return TraceDict(
        trace_id=trace_id,
        session_id=dispatch_ctx.session_id,
        routing_path=dispatch_ctx.routing_path,
        agreement=_agreement_for(dispatch_ctx.routing_path),
        event_input_hash=decision.event_input_hash,
        applied_rule_id=decision.applied_rule_id,
        dispatched_rule_ids=list(decision.dispatched_rule_ids),
        also_matched=list(decision.also_matched),
        next_state=decision.next_state,
        outcome=decision.outcome,
        latency_us=LatencyDict(
            # ``engine`` is None on the LLM path because the deterministic
            # evaluator never ran; ``decision.latency_us`` there is the
            # LLM-call wall time. Symmetric with ``llm_call`` being None
            # on the deterministic path.
            engine=None if is_llm_path else decision.latency_us,
            llm_call=decision.latency_us if is_llm_path else None,
            queue_wait=dispatch_ctx.queue_wait_us,
        ),
        receipt_id=receipt.receipt_id,
        receipt_signature_key_id=receipt.signer_key_id,
        model_id=model_id,
        tool_calls=[],
        decision_traces=decision_traces_serialised,
        skill_doc_hash=skill_doc_hash,
    )


__all__ = [
    "AgreementValue",
    "LatencyDict",
    "ToolCallDict",
    "TraceDict",
    "to_trace_dict",
]
