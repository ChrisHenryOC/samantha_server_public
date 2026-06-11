"""Evaluator — runs dispatched RuleSpecs against a SpecimenContext.

evaluate() is the only entry point to rule evaluation. It enforces the dispatch
boundary by verifying the HMAC token embedded in the DispatchResult.

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

import time
from typing import Any

from samantha_server.canonicalization import (
    CANONICAL_FIELDS,
    COLLECTION_CANONICAL_FIELDS,
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

# Sort CANONICAL_FIELDS once at module load instead of on
# every _collect_canonicalization_traces call (evaluate() hot path).
CANONICAL_FIELDS_SORTED: tuple[str, ...] = tuple(sorted(CANONICAL_FIELDS))


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

    for field in CANONICAL_FIELDS_SORTED:
        if field in COLLECTION_CANONICAL_FIELDS:
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
            # relaxed specimen_type/anatomic_site/fixative/priority to
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
    session_id: str,
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
        The caller's own session identifier.  It is compared against the
        session_id bound in *dispatch*'s HMAC — a mismatch raises
        UndispatchedRuleError so cross-session replay is rejected at the
        evaluate boundary.  Required (making this optional allowed
        callers to silently disarm the guard by omitting the kwarg).
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
        or if *session_id* does not match the session bound in the dispatch
        token (cross-session replay rejected).

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
    # Use the caller-supplied session_id so the cross-session replay guard
    # at verify_dispatch is unconditionally reachable.
    if not verify_dispatch(dispatch, session_id=session_id):
        # Discriminate the session-mismatch case from the
        # HMAC/TTL case so an operator chasing a stale-session caller is not
        # misdirected toward a token-forgery investigation. Session ids are
        # deliberately not echoed (a foreign session id is not the caller's
        # to see).
        if session_id != dispatch.session_id:
            raise UndispatchedRuleError(
                "DispatchResult session mismatch — the dispatch token was issued "
                "for a different session (cross-session replay rejected)"
            )
        raise UndispatchedRuleError(
            "DispatchResult HMAC verification failed or token expired — result "
            "was not produced by list_applicable_rules in this process"
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

    # Compute event_input_hash once here and pass to all
    # helpers so _no_match_decision, _evaluate_accessioning, and
    # _evaluate_first_match share a single computation.
    event_input_hash = compute_event_input_hash(ctx)

    # --- Empty dispatch (no rule fires; hash is still computed for receipt) ---
    if not rules:
        return _no_match_decision(
            ctx, dispatched_rule_ids, start_ns, canon_traces, event_input_hash
        )

    # Determine the step from the first rule (all rules in a dispatch share the
    # same step by invariant of list_applicable_rules).
    step = rules[0].step

    # Belt-and-suspenders: enforce the same-step invariant defensively.
    if not all(r.step == step for r in rules):
        raise RuntimeError(f"mixed-step dispatch: {[r.step for r in rules]}")

    if step == "ACCESSIONING":
        return _evaluate_accessioning(
            rules, ctx, dispatched_rule_ids, start_ns, canon_traces, event_input_hash
        )
    return _evaluate_first_match(
        rules, ctx, dispatched_rule_ids, start_ns, canon_traces, event_input_hash
    )


def _no_match_decision(
    ctx: SpecimenContext,
    dispatched_rule_ids: tuple[str, ...],
    start_ns: int,
    decision_traces: tuple[CanonicalizationTrace, ...] = (),
    event_input_hash: str | None = None,
) -> EngineDecision:
    """Build the no-match / dispatch-empty EngineDecision."""
    if event_input_hash is None:
        event_input_hash = compute_event_input_hash(ctx)
    event_hash = event_input_hash
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
    event_input_hash: str | None = None,
) -> EngineDecision:
    """all_match mode: evaluate every rule, pick highest-severity match."""
    if event_input_hash is None:
        event_input_hash = compute_event_input_hash(ctx)
    matches: list[tuple[Any, Any]] = []

    for rule in rules:
        trace = rule.when.trace(ctx)
        if trace.result:
            matches.append((rule, trace))

    if not matches:
        return _no_match_decision(
            ctx, dispatched_rule_ids, start_ns, decision_traces, event_input_hash
        )

    # Sort by severity ascending (REJECT=0 < HOLD=1 < REVIEW_HOLD=2 < PROCEED=3 < ACCEPT=4).
    # Stable sort: ties keep iteration order (sorted by priority within same severity
    # is not applicable to ACCESSIONING, so first-encountered wins on ties).
    matches.sort(key=lambda pair: SEVERITY_ORDER.get(pair[0].severity or "", 99))

    winner_rule, winner_trace = matches[0]
    also_matched = tuple(r.rule_id for r, _ in matches[1:])
    primitive_traces: dict[str, PrimitiveTrace] = {r.rule_id: t for r, t in matches}

    # Flag-union when winner is REVIEW_HOLD-tier.
    # Union set_flags from matched-but-demoted PROCEED-tier rules so their
    # flags (e.g. MISSING_INFO_PROCEED from ACC-007) carry alongside the
    # winner's flags (e.g. LLM_REVIEW_REQUESTED from ACC-010/011).
    # Rationale: PENDING_LLM_REVIEW -> ACCEPTED resolves directly and never
    # re-runs accessioning, so a dropped PROCEED-tier flag is permanently lost.
    # Only set_flags are unioned; transitions/outcomes/clear_flags from demoted
    # rules are ignored.
    flags_added: tuple[str, ...] = winner_rule.action.set_flags
    if winner_rule.severity == "REVIEW_HOLD":
        seen_flags: set[str] = set(flags_added)
        rescued_flags: list[str] = []
        for demoted_rule, _ in matches[1:]:
            if SEVERITY_ORDER.get(demoted_rule.severity or "", 99) == SEVERITY_ORDER["PROCEED"]:
                for flag in demoted_rule.action.set_flags:
                    if flag not in seen_flags:
                        seen_flags.add(flag)
                        rescued_flags.append(flag)
        if rescued_flags:
            flags_added = flags_added + tuple(rescued_flags)

    event_hash = event_input_hash
    elapsed_us = (time.perf_counter_ns() - start_ns) // 1_000
    return EngineDecision(
        applied_rule_id=winner_rule.rule_id,
        next_state=winner_rule.action.transition,
        flags_added=flags_added,
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
    event_input_hash: str | None = None,
) -> EngineDecision:
    """first_match mode: stop on first rule whose predicate is True."""
    if event_input_hash is None:
        event_input_hash = compute_event_input_hash(ctx)
    event_hash = event_input_hash
    for rule in rules:
        trace = rule.when.trace(ctx)
        if trace.result:
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

    return _no_match_decision(ctx, dispatched_rule_ids, start_ns, decision_traces, event_hash)


__all__ = ["UndispatchedRuleError", "evaluate"]
