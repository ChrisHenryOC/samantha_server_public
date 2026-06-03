"""Tests for samantha_server.engine.transitions.

Migrated from tests/scenarios/test_transitions.py as part of GH-325:
resolve_transition moved from samantha_server.scenarios.transitions to
samantha_server.engine.transitions.
"""

import pytest


def test_concrete_next_state_returned_as_is() -> None:
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.engine.transitions import resolve_transition

    decision = EngineDecision(
        applied_rule_id="ACC-008",
        next_state="ACCEPTED",
        flags_added=(),
        flags_cleared=(),
        outcome="accepted",
        also_matched=(),
        dispatched_rule_ids=("ACC-008",),
        event_input_hash="abc",
        primitive_traces={},
        latency_us=0,
    )
    assert resolve_transition("ACCESSIONING", decision, "order_received") == "ACCEPTED"


def test_advance_sample_prep_from_accepted() -> None:
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.engine.transitions import resolve_transition

    decision = EngineDecision(
        applied_rule_id="SP-001",
        next_state="ADVANCE_SAMPLE_PREP",
        flags_added=(),
        flags_cleared=(),
        outcome="advanced_sample_prep_step",
        also_matched=(),
        dispatched_rule_ids=("SP-001",),
        event_input_hash="abc",
        primitive_traces={},
        latency_us=0,
    )
    assert resolve_transition("ACCEPTED", decision, "grossing_complete") == "SAMPLE_PREP_PROCESSING"


def test_advance_sample_prep_from_missing_info_proceed() -> None:
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.engine.transitions import resolve_transition

    decision = EngineDecision(
        applied_rule_id="SP-001",
        next_state="ADVANCE_SAMPLE_PREP",
        flags_added=(),
        flags_cleared=(),
        outcome="advanced_sample_prep_step",
        also_matched=(),
        dispatched_rule_ids=("SP-001",),
        event_input_hash="abc",
        primitive_traces={},
        latency_us=0,
    )
    assert (
        resolve_transition("MISSING_INFO_PROCEED", decision, "grossing_complete")
        == "SAMPLE_PREP_PROCESSING"
    )


def test_advance_sample_prep_chain() -> None:
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.engine.transitions import resolve_transition

    def make_sp_decision() -> EngineDecision:
        return EngineDecision(
            applied_rule_id="SP-001",
            next_state="ADVANCE_SAMPLE_PREP",
            flags_added=(),
            flags_cleared=(),
            outcome="advanced_sample_prep_step",
            also_matched=(),
            dispatched_rule_ids=("SP-001",),
            event_input_hash="abc",
            primitive_traces={},
            latency_us=0,
        )

    assert (
        resolve_transition("SAMPLE_PREP_PROCESSING", make_sp_decision(), "processing_complete")
        == "SAMPLE_PREP_EMBEDDING"
    )
    assert (
        resolve_transition("SAMPLE_PREP_EMBEDDING", make_sp_decision(), "embedding_complete")
        == "SAMPLE_PREP_SECTIONING"
    )
    assert (
        resolve_transition("SAMPLE_PREP_SECTIONING", make_sp_decision(), "sectioning_complete")
        == "SAMPLE_PREP_QC"
    )


def test_passthrough_he_staining_to_he_qc() -> None:
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.engine.transitions import resolve_transition

    # When no rule fires at HE_STAINING (dispatch_empty), the engine returns
    # current_state as next_state. The resolver must map to HE_QC via the
    # passthrough table.
    decision = EngineDecision(
        applied_rule_id=None,
        next_state="HE_STAINING",  # engine echoes current_state on dispatch_empty
        flags_added=(),
        flags_cleared=(),
        outcome="dispatch_empty",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="abc",
        primitive_traces={},
        latency_us=0,
    )
    assert resolve_transition("HE_STAINING", decision, "he_staining_complete") == "HE_QC"


def test_passthrough_ihc_staining_to_ihc_qc() -> None:
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.engine.transitions import resolve_transition

    decision = EngineDecision(
        applied_rule_id=None,
        next_state="IHC_STAINING",
        flags_added=(),
        flags_cleared=(),
        outcome="dispatch_empty",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="abc",
        primitive_traces={},
        latency_us=0,
    )
    assert resolve_transition("IHC_STAINING", decision, "ihc_staining_complete") == "IHC_QC"


def test_retry_sample_prep_returns_current_state() -> None:
    """RETRY_SAMPLE_PREP is a self-loop — returns current_state."""
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.engine.transitions import resolve_transition

    for state in ("SAMPLE_PREP_PROCESSING", "SAMPLE_PREP_EMBEDDING", "SAMPLE_PREP_SECTIONING"):
        decision = EngineDecision(
            applied_rule_id="SP-002",
            next_state="RETRY_SAMPLE_PREP",
            flags_added=(),
            flags_cleared=(),
            outcome="retried_sample_prep_step",
            also_matched=(),
            dispatched_rule_ids=("SP-002",),
            event_input_hash="abc",
            primitive_traces={},
            latency_us=0,
        )
        result = resolve_transition(state, decision, "processing_complete")
        assert result == state, f"Expected {state}, got {result}"


def test_resolve_missing_info_clears_flag_to_resulting() -> None:
    """RESOLVE_MISSING_INFO with billing info clears MISSING_INFO_PROCEED → RESULTING."""
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.engine.transitions import resolve_transition

    decision = EngineDecision(
        applied_rule_id="RES-002",
        next_state="RESOLVE_MISSING_INFO",
        flags_added=(),
        flags_cleared=(),  # spec says empty; runtime clears it
        outcome="evaluated_missing_info_received",
        also_matched=(),
        dispatched_rule_ids=("RES-002",),
        event_input_hash="abc",
        primitive_traces={},
        latency_us=0,
    )
    # Billing info received → flag cleared → RESULTING
    result = resolve_transition(
        "RESULTING_HOLD", decision, "missing_info_received", accumulated_flags=frozenset()
    )
    assert result == "RESULTING"


def test_resolve_missing_info_flag_still_set_stays_resulting_hold() -> None:
    """RESOLVE_MISSING_INFO with flag still set stays in RESULTING_HOLD."""
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.engine.transitions import resolve_transition

    decision = EngineDecision(
        applied_rule_id="RES-002",
        next_state="RESOLVE_MISSING_INFO",
        flags_added=(),
        flags_cleared=(),
        outcome="evaluated_missing_info_received",
        also_matched=(),
        dispatched_rule_ids=("RES-002",),
        event_input_hash="abc",
        primitive_traces={},
        latency_us=0,
    )
    # Flag still set after info received → RESULTING_HOLD
    result = resolve_transition(
        "RESULTING_HOLD",
        decision,
        "missing_info_received",
        accumulated_flags=frozenset({"MISSING_INFO_PROCEED"}),
    )
    assert result == "RESULTING_HOLD"


def test_advance_sample_prep_unmapped_state_raises() -> None:
    """ADVANCE_SAMPLE_PREP with a current_state not in the mapping must raise ValueError."""
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.engine.transitions import resolve_transition

    decision = EngineDecision(
        applied_rule_id="SP-001",
        next_state="ADVANCE_SAMPLE_PREP",
        flags_added=(),
        flags_cleared=(),
        outcome="advanced_sample_prep_step",
        also_matched=(),
        dispatched_rule_ids=("SP-001",),
        event_input_hash="abc",
        primitive_traces={},
        latency_us=0,
    )
    with pytest.raises(ValueError, match="ADVANCE_SAMPLE_PREP has no mapping for current_state"):
        resolve_transition("HE_STAINING", decision, "grossing_complete")


def test_passthrough_missing_info_hold_to_accessioning() -> None:
    """MISSING_INFO_HOLD + missing_info_received must resolve to ACCESSIONING via passthrough table.

    MISSING_INFO_HOLD is absent from both STATE_TO_STEP and rules_by_applies_at, so the
    dispatcher always returns dispatch_empty. The passthrough table must cover the outgoing
    edge to ACCESSIONING so the workflow can advance.
    """
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.engine.transitions import resolve_transition

    decision = EngineDecision(
        applied_rule_id=None,
        next_state="MISSING_INFO_HOLD",  # engine echoes current_state on dispatch_empty
        flags_added=(),
        flags_cleared=(),
        outcome="dispatch_empty",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="abc",
        primitive_traces={},
        latency_us=0,
    )
    assert (
        resolve_transition("MISSING_INFO_HOLD", decision, "missing_info_received") == "ACCESSIONING"
    )


def test_unknown_symbolic_transition_raises() -> None:
    from samantha_server.engine.decision import EngineDecision
    from samantha_server.engine.transitions import resolve_transition

    decision = EngineDecision(
        applied_rule_id="XX-001",
        next_state="UNKNOWN_SYMBOLIC",
        flags_added=(),
        flags_cleared=(),
        outcome="something",
        also_matched=(),
        dispatched_rule_ids=("XX-001",),
        event_input_hash="abc",
        primitive_traces={},
        latency_us=0,
    )
    with pytest.raises(ValueError, match="UNKNOWN_SYMBOLIC"):
        resolve_transition("SOME_STATE", decision, "some_event")
