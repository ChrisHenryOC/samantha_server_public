"""Evaluator — runs dispatched RuleSpecs against a SpecimenContext.

evaluate() is the only entry point to rule evaluation. It enforces the dispatch
boundary by verifying the HMAC token embedded in the DispatchResult.

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

import time
from typing import Any

from samantha_server.canonicalization import (
    _COLLECTION_CANONICAL_FIELDS,
    CANONICAL_FIELDS,
    canonicalize,
)
from samantha_server.engine.decision import (
    CanonicalizationTrace,
    EngineDecision,
    compute_event_input_hash,
)
from samantha_server.engine.dispatch_constants import SEVERITY_ORDER
from samantha_server.engine.dispatcher import DispatchResult, verify_dispatch
from samantha_server.errors import SamanthaError
from samantha_server.models.context import SpecimenContext
from samantha_server.primitives.trace import PrimitiveTrace
from samantha_server.rules.spec import RuleSpec


class UndispatchedRuleError(SamanthaError):
    """Raised when evaluate() is called with a DispatchResult whose HMAC token
    does not match the expected value — i.e. it was not produced by
    list_applicable_rules in this process."""


def _collect_canonicalization_traces(
    ctx: SpecimenContext,
) -> tuple[CanonicalizationTrace, ...]:
    """Scan canonicalizable Order fields and return a CanonicalizationTrace
    for each field value that is not in the project pick list.

    Preflight design: this function runs **once** per ``evaluate()`` call,
    before any rule iteration.  Because it is called exactly once regardless
    of how many rules match (or miss), deduplication at the rule-iteration
    level is unnecessary — the architecture prevents duplicates inherently.
    Deduplication by ``(field, raw_value)`` within this function handles the
    ``ordered_tests`` collection case where the same unknown test code may
    appear multiple times in a single tuple.

    The scan walks ``Order`` fields (data side) only.  Rule-side predicate
    literals are written by rule authors in canonical form and are never
    traced here — the asymmetry is intentional and documented in the module
    docstring of ``canonicalization.py``.
    """
    traces: list[CanonicalizationTrace] = []
    seen: set[tuple[str, str]] = set()

    order = ctx.order

    for field in sorted(CANONICAL_FIELDS):
        if field in _COLLECTION_CANONICAL_FIELDS:
            # Collection field — canonicalize each element independently.
            raw_collection: tuple[str, ...] = getattr(order, field)
            for raw in raw_collection:
                key = (field, str(raw))  # str() coercion ensures type-stable keys
                if key in seen:
                    continue
                result = canonicalize(field, raw)
                if result.was_unknown:
                    seen.add(key)
                    traces.append(
                        CanonicalizationTrace(
                            field=field,
                            raw_value=raw,
                            canonical_value_attempted=result.canonical,
                        )
                    )
        else:
            # GH-105 relaxed specimen_type/anatomic_site/fixative/priority to
            # str | None on Order, so this branch can return None — the guard
            # below is load-bearing.
            raw_str: str | None = getattr(order, field)
            if raw_str is None:
                continue
            key = (field, str(raw_str))  # str() coercion ensures type-stable keys
            if key in seen:
                continue
            result = canonicalize(field, raw_str)
            if result.was_unknown:
                seen.add(key)
                traces.append(
                    CanonicalizationTrace(
                        field=field,
                        raw_value=raw_str,
                        canonical_value_attempted=result.canonical,
                    )
                )

    return tuple(traces)


def evaluate(
    dispatch: DispatchResult,
    ctx: SpecimenContext,
    *,
    session_id: str | None = None,
    rule_id_filter: str | None = None,
) -> EngineDecision:
    """Evaluate the rules in *dispatch* against *ctx* and return an EngineDecision.

    Parameters
    ----------
    dispatch:
        Result of list_applicable_rules; HMAC-verified before evaluation.
    ctx:
        The specimen context to evaluate rules against.
    session_id:
        The caller's session identifier.  When provided, it is compared against
        the session_id bound in *dispatch*'s HMAC — a mismatch causes
        UndispatchedRuleError so cross-session replay is rejected at the
        evaluate boundary.  When ``None`` (the default), falls back to
        ``dispatch.session_id`` (backward-compatible with callers that verified
        the session upstream or do not require cross-session protection).
        Production callers (the router in api/routing.py, scenarios/replay)
        must pass the caller's own session_id to activate the explicit guard.
    rule_id_filter:
        When not None, restrict evaluation to the single rule whose
        ``rule_id`` matches this value.  If no rule with this id is present
        the evaluator behaves like an empty dispatch (outcome='dispatch_empty').
        ``dispatched_rule_ids`` in the returned EngineDecision always carries
        the *full* original dispatch list regardless of filtering (G9 contract).

    Raises
    ------
    UndispatchedRuleError
        If *dispatch* was not produced by list_applicable_rules (HMAC failure),
        or if the caller-supplied *session_id* does not match the session
        bound in the dispatch token (cross-session replay rejected).

    Notes
    -----
    ACCESSIONING step: all_match mode — evaluate every rule, collect every
    match, apply the highest-severity match (lowest SEVERITY_ORDER index).
    Record non-applied matches in also_matched.

    All other steps: first_match mode by ascending priority (order preserved
    from RuleIndex). Stop on first match.

    If no rule matches: outcome='dispatch_empty', applied_rule_id=None,
    next_state=ctx.current_state.

    Every decision path returns an EngineDecision; this function never returns
    None or raises silently.
    """
    # --- Boundary enforcement via HMAC ---
    # Use the caller-supplied session_id (not dispatch.session_id) so the
    # cross-session replay guard at verify_dispatch is reachable.
    # When session_id is None (backward compat), fall back to dispatch.session_id.
    effective_session_id = session_id if session_id is not None else dispatch.session_id
    if not verify_dispatch(dispatch, session_id=effective_session_id):
        raise UndispatchedRuleError(
            "DispatchResult HMAC verification failed — result was not produced by "
            "list_applicable_rules in this process"
        )

    start_ns = time.perf_counter_ns()
    # G9: dispatched_rule_ids always reflects the full original dispatch.
    dispatched_rule_ids = tuple(r.rule_id for r in dispatch.rules)
    # Apply the optional filter for iteration only.
    rules = (
        tuple(r for r in dispatch.rules if r.rule_id == rule_id_filter)
        if rule_id_filter is not None
        else dispatch.rules
    )

    # Preflight: collect canonicalization warnings for this context.
    # Runs once per evaluate() call regardless of rule matching outcome.
    canon_traces = _collect_canonicalization_traces(ctx)

    # --- Empty dispatch (no rule fires; hash is still computed for receipt) ---
    if not rules:
        return _no_match_decision(ctx, dispatched_rule_ids, start_ns, canon_traces)

    # Determine the step from the first rule (all rules in a dispatch share the
    # same step by invariant of list_applicable_rules).
    step = rules[0].step

    # Belt-and-suspenders: enforce the same-step invariant defensively.
    if not all(r.step == step for r in rules):
        raise RuntimeError(f"mixed-step dispatch: {[r.step for r in rules]}")

    if step == "ACCESSIONING":
        return _evaluate_accessioning(rules, ctx, dispatched_rule_ids, start_ns, canon_traces)
    return _evaluate_first_match(rules, ctx, dispatched_rule_ids, start_ns, canon_traces)


def _no_match_decision(
    ctx: SpecimenContext,
    dispatched_rule_ids: tuple[str, ...],
    start_ns: int,
    decision_traces: tuple[CanonicalizationTrace, ...] = (),
) -> EngineDecision:
    """Build the no-match / dispatch-empty EngineDecision."""
    event_hash = compute_event_input_hash(ctx)
    elapsed_us = (time.perf_counter_ns() - start_ns) // 1_000
    return EngineDecision(
        applied_rule_id=None,
        next_state=ctx.current_state,
        flags_added=(),
        flags_cleared=(),
        outcome="dispatch_empty",
        also_matched=(),
        dispatched_rule_ids=dispatched_rule_ids,
        event_input_hash=event_hash,
        primitive_traces={},
        latency_us=elapsed_us,
        decision_traces=decision_traces,
        order_id=ctx.order.order_id,
    )


def _evaluate_accessioning(
    rules: tuple[RuleSpec, ...],
    ctx: SpecimenContext,
    dispatched_rule_ids: tuple[str, ...],
    start_ns: int,
    decision_traces: tuple[CanonicalizationTrace, ...] = (),
) -> EngineDecision:
    """all_match mode: evaluate every rule, pick highest-severity match."""
    matches: list[tuple[Any, Any]] = []

    for rule in rules:
        trace = rule.when.trace(ctx)
        if trace.result:
            matches.append((rule, trace))

    if not matches:
        return _no_match_decision(ctx, dispatched_rule_ids, start_ns, decision_traces)

    # Sort by severity ascending (REJECT=0 < HOLD=1 < PROCEED=2 < ACCEPT=3).
    # Stable sort: ties keep iteration order (sorted by priority within same severity
    # is not applicable to ACCESSIONING, so first-encountered wins on ties).
    matches.sort(key=lambda pair: SEVERITY_ORDER.get(pair[0].severity or "", 99))

    winner_rule, winner_trace = matches[0]
    also_matched = tuple(r.rule_id for r, _ in matches[1:])
    primitive_traces: dict[str, PrimitiveTrace] = {r.rule_id: t for r, t in matches}

    event_hash = compute_event_input_hash(ctx)
    elapsed_us = (time.perf_counter_ns() - start_ns) // 1_000
    return EngineDecision(
        applied_rule_id=winner_rule.rule_id,
        next_state=winner_rule.action.transition,
        flags_added=winner_rule.action.set_flags,
        flags_cleared=winner_rule.action.clear_flags,
        outcome=winner_rule.action.outcome,
        also_matched=also_matched,
        dispatched_rule_ids=dispatched_rule_ids,
        event_input_hash=event_hash,
        primitive_traces=primitive_traces,
        latency_us=elapsed_us,
        decision_traces=decision_traces,
        order_id=ctx.order.order_id,
    )


def _evaluate_first_match(
    rules: tuple[RuleSpec, ...],
    ctx: SpecimenContext,
    dispatched_rule_ids: tuple[str, ...],
    start_ns: int,
    decision_traces: tuple[CanonicalizationTrace, ...] = (),
) -> EngineDecision:
    """first_match mode: stop on first rule whose predicate is True."""
    for rule in rules:
        trace = rule.when.trace(ctx)
        if trace.result:
            event_hash = compute_event_input_hash(ctx)
            elapsed_us = (time.perf_counter_ns() - start_ns) // 1_000
            return EngineDecision(
                applied_rule_id=rule.rule_id,
                next_state=rule.action.transition,
                flags_added=rule.action.set_flags,
                flags_cleared=rule.action.clear_flags,
                outcome=rule.action.outcome,
                also_matched=(),
                dispatched_rule_ids=dispatched_rule_ids,
                event_input_hash=event_hash,
                primitive_traces={rule.rule_id: trace},
                latency_us=elapsed_us,
                decision_traces=decision_traces,
                order_id=ctx.order.order_id,
            )

    return _no_match_decision(ctx, dispatched_rule_ids, start_ns, decision_traces)


__all__ = ["UndispatchedRuleError", "evaluate"]
