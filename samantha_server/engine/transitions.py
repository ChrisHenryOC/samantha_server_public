"""Symbolic transition resolver for the deterministic dispatch path.

Translates EngineDecision.next_state values (which may be symbolic tokens
like ADVANCE_SAMPLE_PREP) into concrete workflow state strings.

Also handles pass-through transitions — states where no rule fires but
an implicit state machine edge exists (e.g., HE_STAINING → HE_QC on
he_staining_complete when the engine returns dispatch_empty).

No LLM imports. Deterministic path only.
"""

from __future__ import annotations

from samantha_server.engine.decision import EngineDecision
from samantha_server.models.context import VALID_STATES
from samantha_server.rules.loader import RuleIndex

# Sample prep linear progression for ADVANCE_SAMPLE_PREP resolution.
# Source: samantha_server/rules/specs/SP-001.yaml comment.
# ACCEPTED and MISSING_INFO_PROCEED both advance to SAMPLE_PREP_PROCESSING
# (they are the entry points into sample prep after grossing).
_ADVANCE_SAMPLE_PREP: dict[str, str] = {
    "ACCEPTED": "SAMPLE_PREP_PROCESSING",
    "MISSING_INFO_PROCEED": "SAMPLE_PREP_PROCESSING",
    "SAMPLE_PREP_PROCESSING": "SAMPLE_PREP_EMBEDDING",
    "SAMPLE_PREP_EMBEDDING": "SAMPLE_PREP_SECTIONING",
    "SAMPLE_PREP_SECTIONING": "SAMPLE_PREP_QC",
}

# Pass-through transitions: (current_state, event_type) → next_state.
# Fire when the engine returns dispatch_empty but the state machine
# (workflow_states.yaml) defines an event-driven outgoing edge. Audited
# complete set, covering both routes to dispatch_empty:
#   - State absent from both STATE_TO_STEP and rules_by_applies_at, so no
#     rule is ever dispatched: MISSING_INFO_HOLD, HE_STAINING.
#   - State present in rules_by_applies_at but the dispatched rule's
#     predicate misses: IHC_STAINING (IHC-001 fires only on HER2 reject;
#     the normal ihc_staining_complete is dispatch_empty).
# DO_NOT_PROCESS→ORDER_TERMINATED is intentionally excluded — it has no
# triggering event_type (synchronous terminal collapse).
_PASSTHROUGH_TRANSITIONS: dict[tuple[str, str], str] = {
    ("HE_STAINING", "he_staining_complete"): "HE_QC",
    ("IHC_STAINING", "ihc_staining_complete"): "IHC_QC",
    ("MISSING_INFO_HOLD", "missing_info_received"): "ACCESSIONING",
}

# Symbolic tokens that require special resolution (not concrete state names).
_SYMBOLIC_TOKENS: frozenset[str] = frozenset(
    {"ADVANCE_SAMPLE_PREP", "RESOLVE_MISSING_INFO", "RETRY_SAMPLE_PREP"}
)

# Pass-through event_types — the second element of each _PASSTHROUGH_TRANSITIONS key.
# Used by get_known_event_types to include mechanical pass-through events in the
# known set even when the rule index has no rules covering them.
_PASSTHROUGH_EVENT_TYPES: frozenset[str] = frozenset(
    event_type for _, event_type in _PASSTHROUGH_TRANSITIONS
)


def get_known_event_types(rule_index: RuleIndex) -> frozenset[str]:
    """Return the set of event_types known to this engine configuration.

    Derived from the loaded rule index (union of all rule.event_type tuples) plus the
    mechanical pass-through event_types from _PASSTHROUGH_TRANSITIONS. Used by the
    dispatch_empty observability check in routing.py to distinguish an unknown/unsupported
    event_type from a legitimate no-rule-match.
    """
    rule_event_types: frozenset[str] = frozenset(
        et for rule in rule_index.all_rules for et in rule.event_type
    )
    return rule_event_types | _PASSTHROUGH_EVENT_TYPES


def resolve_transition(
    current_state: str,
    decision: EngineDecision,
    event_type: str,
    *,
    accumulated_flags: frozenset[str] | None = None,
) -> str:
    """Resolve *decision.next_state* to a concrete workflow state string.

    Cases:
    1. ADVANCE_SAMPLE_PREP → looked up in _ADVANCE_SAMPLE_PREP using *current_state*.
    2. RESOLVE_MISSING_INFO → branching transition for RES-002:
       if MISSING_INFO_PROCEED is NOT in *accumulated_flags* (i.e., was cleared by the
       action handler) → RESULTING; else → RESULTING_HOLD.
       Pass *accumulated_flags* as the flags AFTER the action handler would have run.
    3. dispatch_empty (applied_rule_id is None, next_state echoes current_state)
       → checked against _PASSTHROUGH_TRANSITIONS keyed by (current_state, event_type).
       If no entry exists the echoed current_state is returned (terminal / unrecognised).
    4. Concrete next_state → returned as-is.

    Raises ValueError when a symbolic token has no mapping for the given current_state,
    or when an unrecognised symbolic token is returned.
    """
    raw = decision.next_state

    # Case: symbolic ADVANCE_SAMPLE_PREP
    if raw == "ADVANCE_SAMPLE_PREP":
        resolved = _ADVANCE_SAMPLE_PREP.get(current_state)
        if resolved is None:
            raise ValueError(
                f"ADVANCE_SAMPLE_PREP has no mapping for current_state={current_state!r}"
            )
        return resolved

    # Case: symbolic RETRY_SAMPLE_PREP (SP-002 self-loop).
    # The order retries the current sub-state; next_state = current_state.
    if raw == "RETRY_SAMPLE_PREP":
        return current_state

    # Case: symbolic RESOLVE_MISSING_INFO (RES-002 branching transition).
    # The action handler cleared MISSING_INFO_PROCEED if billing info was received.
    # *accumulated_flags* reflects the post-handler flag set.
    if raw == "RESOLVE_MISSING_INFO":
        flags = accumulated_flags if accumulated_flags is not None else frozenset()
        if "MISSING_INFO_PROCEED" not in flags:
            return "RESULTING"
        return "RESULTING_HOLD"

    # Case: unrecognised symbolic token (not a known workflow state).
    # Detect by checking if the token is absent from VALID_STATES and not a known symbolic.
    if raw not in VALID_STATES and raw not in _SYMBOLIC_TOKENS:
        raise ValueError(
            f"Unknown symbolic transition {raw!r} returned by engine "
            f"(current_state={current_state!r}, event_type={event_type!r})"
        )

    # Case: dispatch_empty — engine echoed current_state
    if decision.applied_rule_id is None and raw == current_state:
        passthrough = _PASSTHROUGH_TRANSITIONS.get((current_state, event_type))
        if passthrough is not None:
            return passthrough
        # No passthrough registered → remain in current_state (terminal / gap)
        return raw

    # Case: concrete next_state
    return raw
