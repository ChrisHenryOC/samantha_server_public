"""Orchestrator-side event dispatch — dispatch_event().

dispatch_event() is the single production entry point that:
1. Determines the routing path (llm or deterministic).
2. Calls the appropriate handler.
3. Stamps session_id on the EngineDecision.
4. Calls emit_receipt() — the orchestrator-side chokepoint.
5. Returns (EngineDecision, EventDispatchContext, SignedReceipt).

Receipt-emission contract: every EngineDecision returned through this
function emits exactly one receipt via emit_receipt(). The two documented
no-receipt paths (backpressure_rejection, pre_dequeue_cancellation) never
reach this function. All event_types — including deterministic events that
match no rule (dispatch_empty) — return normally with a receipt.

Architectural invariants:
- LLM handler imports happen at module scope because routing.py is the
  orchestrator-side dispatcher; LLM imports are expected here and do NOT
  violate deterministic-path purity (engine/, rules/, primitives/ must not
  import from api/).
- emit_receipt is the sole call to sign_decision in the orchestrator layer.
- The deterministic branch (GH-324) must not reference llm_client.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Literal

from samantha_server.api.event_context import EventDispatchContext
from samantha_server.api.preflight import PreflightMissing, preflight
from samantha_server.api.receipt_emission import emit_receipt
from samantha_server.api.receipt_writer import ReceiptWriterProtocol
from samantha_server.engine.action_handlers import apply_runtime_flag_clearing
from samantha_server.engine.decision import EngineDecision
from samantha_server.engine.dispatcher import list_applicable_rules
from samantha_server.engine.evaluator import evaluate
from samantha_server.engine.transitions import get_known_event_types, resolve_transition
from samantha_server.llm.client import LLMClient
from samantha_server.llm.handlers import (
    handle_clarification,
    handle_clinical_query,
    handle_pending_llm_review,
)
from samantha_server.models.context import SpecimenContext
from samantha_server.observability.counters import CounterRegistry
from samantha_server.queue.priority import EventPriority
from samantha_server.receipts.signing import SignedReceipt
from samantha_server.rules.loader import RuleIndex
from samantha_server.scenarios.loader import Scenario
from samantha_server.skills.loader import SkillSpec

_log = logging.getLogger(__name__)

# Narrow literal type for routing_path — only "llm" and "deterministic" are valid
# classifications for events that complete dispatch. All event_types that reach
# _dispatch_event_core route to one of these two paths; "refused" is never
# a runtime value here.
RoutingPath = Literal["deterministic", "llm"]


async def dispatch_event(
    ctx: SpecimenContext,
    *,
    session_id: str,
    priority: EventPriority,
    queue_wait_us: int,
    receipt_writer: ReceiptWriterProtocol,
    write_lock: asyncio.Lock,
    counters: CounterRegistry,
    llm_client: LLMClient,
    scenarios_index: Mapping[str, Scenario],
    skills_index: Mapping[str, SkillSpec],
    rule_index: RuleIndex,
    prompt_timestamp: str | None = None,
) -> tuple[EngineDecision, EventDispatchContext, SignedReceipt]:
    """Route *ctx* to the appropriate handler and emit a signed receipt.

    Parameters
    ----------
    ctx:
        The SpecimenContext to dispatch.
    session_id:
        Caller-supplied session identifier. Stamped onto the EngineDecision.
    priority:
        EventPriority at enqueue time. Stored on EventDispatchContext.
    queue_wait_us:
        Time the event spent in the priority queue (microseconds).
    receipt_writer:
        Shared ReceiptWriterProtocol implementation (e.g., ReceiptWriter).
    write_lock:
        asyncio.Lock serializing writes to receipt_writer.
    counters:
        CounterRegistry for silent-loss observability.
    llm_client:
        Initialized LLM client.
    scenarios_index:
        Mapping of scenario_id → Scenario.
    skills_index:
        Mapping of skill_name → SkillSpec.
    rule_index:
        Loaded RuleIndex for deterministic dispatch.

    Returns
    -------
    tuple[EngineDecision, EventDispatchContext, SignedReceipt]
        The decision, the orchestrator-owned dispatch context, and the
        signed receipt that was persisted.

    Notes
    -----
    This function never raises for unrecognized event_types. Deterministic
    events that match no rule produce a dispatch_empty EngineDecision with
    a receipt, instead of raising NotImplementedError (GH-324).
    """
    return await _dispatch_event_core(
        ctx,
        session_id=session_id,
        priority=priority,
        queue_wait_us=queue_wait_us,
        receipt_writer=receipt_writer,
        write_lock=write_lock,
        counters=counters,
        llm_client=llm_client,
        scenarios_index=scenarios_index,
        skills_index=skills_index,
        rule_index=rule_index,
        prompt_timestamp=prompt_timestamp,
    )


async def _dispatch_event_core(
    ctx: SpecimenContext,
    *,
    session_id: str,
    priority: EventPriority,
    queue_wait_us: int,
    receipt_writer: ReceiptWriterProtocol,
    write_lock: asyncio.Lock,
    counters: CounterRegistry,
    llm_client: LLMClient,
    scenarios_index: Mapping[str, Scenario],
    skills_index: Mapping[str, SkillSpec],
    rule_index: RuleIndex,
    prompt_timestamp: str | None = None,
) -> tuple[EngineDecision, EventDispatchContext, SignedReceipt]:
    """Core dispatch logic — no span management. Called by both production and replay paths."""
    # Determine routing path and dispatch to the appropriate handler.
    # State-first precedence mirrors router.py::route().
    routing_path: RoutingPath
    decision: EngineDecision

    if ctx.current_state == "PENDING_LLM_REVIEW":
        routing_path = "llm"
        decision = handle_pending_llm_review(ctx, llm_client, skills_index)
    else:
        preflight_result = preflight(ctx)
        if isinstance(preflight_result, PreflightMissing):
            routing_path = "llm"
            decision = handle_clarification(
                ctx,
                preflight_result=preflight_result,
                llm_client=llm_client,
            )
        elif ctx.event.event_type == "clinical_query":
            routing_path = "llm"
            decision = handle_clinical_query(
                ctx,
                llm_client,
                scenarios_index,
                skills_index,
                prompt_timestamp=prompt_timestamp,
                counters=counters,
            )
        else:
            # Deterministic path: run the dispatcher gate then evaluate.
            # Deterministic-path purity: no llm_client reference here.
            #
            # samantha_server.config is imported lazily to avoid triggering
            # the test-sentinel check at collection time.
            import samantha_server.config as _cfg

            routing_path = "deterministic"
            dispatch = list_applicable_rules(
                ctx,
                rule_index,
                session_id=session_id,
                ttl_sec=_cfg.DISPATCH_TOKEN_TTL_SEC,
            )
            decision = evaluate(dispatch, ctx, session_id=session_id)
            # Resolve symbolic transitions (ADVANCE_SAMPLE_PREP, RETRY_SAMPLE_PREP,
            # RESOLVE_MISSING_INFO) to concrete workflow states before signing the receipt.
            # LLM-path handlers already return concrete states; only the deterministic
            # branch needs this step.
            # Mirror the replay harness sequence (scenarios/replay.py ~L1158-1169):
            # 1. Apply spec-level flag changes from the decision.
            # 2. Apply runtime action-handler flag clearing (GH-328).
            # 3. Resolve symbolic transitions against the post-handler flag set.
            post_flags = (ctx.flags - set(decision.flags_cleared)) | set(decision.flags_added)
            post_flags = apply_runtime_flag_clearing(decision, ctx.event.event_data, post_flags)
            resolved_state = resolve_transition(
                ctx.current_state,
                decision,
                ctx.event.event_type,
                accumulated_flags=post_flags,
            )
            decision = decision.model_copy(update={"next_state": resolved_state})

            # Unknown-event observability (GH-326): when dispatch_empty produced no
            # state change AND the event_type is not in the known set, this is a
            # signal that an unrecognised event leaked into the engine — not a
            # legitimate no-rule-match on a known event.
            if (
                decision.applied_rule_id is None
                and resolved_state == ctx.current_state
                and ctx.event.event_type not in get_known_event_types(rule_index)
            ):
                counters.dispatch_unknown_event_type.increment()
                # %r is deliberate (not %s): repr-escapes control chars / ANSI in the
                # caller-supplied event_type against log forging (CWE-117). The field is
                # also capped at 100 chars by the Event model validator. current_state is
                # validated against the closed VALID_STATES set, so it needs no escaping.
                _log.warning(
                    "dispatch_empty for unrecognised event_type=%r in state=%r; "
                    "no rule matches and no pass-through registered",
                    ctx.event.event_type,
                    ctx.current_state,
                )

    # Stamp session_id on the decision.
    stamped = decision.model_copy(update={"session_id": session_id})

    # Emit receipt — the orchestrator-side chokepoint.
    receipt = await emit_receipt(
        stamped,
        session_id,
        receipt_writer=receipt_writer,
        write_lock=write_lock,
        counters=counters,
    )

    dispatch_ctx = EventDispatchContext(
        session_id=session_id,
        priority=priority,
        routing_path=routing_path,
        queue_wait_us=queue_wait_us,
    )

    return stamped, dispatch_ctx, receipt


__all__ = ["dispatch_event"]
