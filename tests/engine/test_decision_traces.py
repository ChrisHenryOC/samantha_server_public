"""Tests for decision_traces typed union on EngineDecision (Phase 2 Step 6)."""

from __future__ import annotations

import typing

import pytest

# ---------------------------------------------------------------------------
# CanonicalizationTrace
# ---------------------------------------------------------------------------


def test_canonicalization_trace_kind_discriminator() -> None:
    from samantha_server.engine.decision import CanonicalizationTrace

    t = CanonicalizationTrace(
        field="patient_name",
        raw_value="  John Doe  ",
        canonical_value_attempted="John Doe",
    )
    assert t.kind == "canonicalization_warning"


def test_canonicalization_trace_is_frozen() -> None:
    from pydantic import ValidationError

    from samantha_server.engine.decision import CanonicalizationTrace

    t = CanonicalizationTrace(
        field="x",
        raw_value="r",
        canonical_value_attempted="c",
    )
    with pytest.raises((AttributeError, TypeError, ValidationError)):
        t.field = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# QueryTrace
# ---------------------------------------------------------------------------


def test_query_trace_kind_discriminator() -> None:
    from samantha_server.engine.decision import QueryTrace

    t = QueryTrace(
        query_text_hash="abc123",
        skill_doc_hash="def456",
        scenarios_cited=("SC-001",),
        model_id="llama-3b",
        response_text_hash="ghi789",
    )
    assert t.kind == "query"


def test_query_trace_scenarios_cited_is_tuple() -> None:
    from samantha_server.engine.decision import QueryTrace

    t = QueryTrace(
        query_text_hash="a",
        skill_doc_hash="b",
        scenarios_cited=("SC-001", "SC-002"),
        model_id="m",
        response_text_hash="r",
    )
    assert isinstance(t.scenarios_cited, tuple)
    assert t.scenarios_cited == ("SC-001", "SC-002")


# ---------------------------------------------------------------------------
# ClarificationTrace
# ---------------------------------------------------------------------------


def test_clarification_trace_kind_discriminator() -> None:
    from samantha_server.engine.decision import ClarificationTrace

    t = ClarificationTrace(
        missing_fields=("patient_name",),
        suggested_values={"patient_name": "Unknown"},
    )
    assert t.kind == "clarification"


def test_clarification_trace_missing_fields_is_tuple() -> None:
    from samantha_server.engine.decision import ClarificationTrace

    t = ClarificationTrace(
        missing_fields=("f1", "f2"),
        suggested_values={},
    )
    assert isinstance(t.missing_fields, tuple)


# ---------------------------------------------------------------------------
# RefusalTrace
# ---------------------------------------------------------------------------


def test_refusal_trace_kind_discriminator() -> None:
    from samantha_server.engine.decision import RefusalTrace

    t = RefusalTrace(
        refusal_reason="STAGE_PRE_SKILL_UNAVAILABLE",
        refusal_stage="PRE",
        judge_verdict=None,
    )
    assert t.kind == "refusal"


def test_refusal_trace_stage_pre_rejects_judge_verdict() -> None:
    from pydantic import ValidationError

    from samantha_server.engine.decision import RefusalTrace

    with pytest.raises((ValidationError, ValueError)):
        RefusalTrace(
            refusal_reason="STAGE_PRE_SKILL_UNAVAILABLE",
            refusal_stage="PRE",
            judge_verdict="uncertain",  # invalid on stage PRE
        )


def test_refusal_trace_stage_pre_rejects_alternatives() -> None:
    from pydantic import ValidationError

    from samantha_server.engine.decision import RefusalTrace

    with pytest.raises((ValidationError, ValueError)):
        RefusalTrace(
            refusal_reason="STAGE_PRE_LLM_UNAVAILABLE",
            refusal_stage="PRE",
            judge_verdict=None,
            alternatives=("ACC-001",),  # invalid on stage PRE
        )


def test_refusal_trace_stage_pre_reason_requires_stage_pre() -> None:
    """M-11: STAGE_PRE_* reasons must use refusal_stage='PRE'."""
    from pydantic import ValidationError

    from samantha_server.engine.decision import RefusalTrace

    with pytest.raises((ValidationError, ValueError)):
        RefusalTrace(
            refusal_reason="STAGE_PRE_SKILL_UNAVAILABLE",
            refusal_stage="A",  # wrong stage for STAGE_PRE_ reason
            judge_verdict=None,
        )


# ---------------------------------------------------------------------------
# LLMReviewTrace
# ---------------------------------------------------------------------------


def test_llm_review_trace_kind_discriminator() -> None:
    from samantha_server.engine.decision import LLMReviewTrace

    t = LLMReviewTrace(
        skill_doc_hash="abc",
        model_id="llama-3b",
        response_text_hash="def",
        disposition="accept",
    )
    assert t.kind == "llm_review"


def test_llm_review_trace_disposition_values() -> None:
    from samantha_server.engine.decision import LLMReviewTrace

    for d in ("accept", "reject", "escalate"):
        t = LLMReviewTrace(
            skill_doc_hash="x",
            model_id="m",
            response_text_hash="y",
            disposition=d,  # type: ignore[arg-type]
        )
        assert t.disposition == d


# ---------------------------------------------------------------------------
# EngineDecision.decision_traces integration
# ---------------------------------------------------------------------------


def test_engine_decision_defaults_decision_traces_to_empty_tuple() -> None:
    from samantha_server.engine.decision import EngineDecision

    d = EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="dispatch_empty",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="a" * 64,
        primitive_traces={},
        latency_us=0,
    )
    assert d.decision_traces == ()


def test_engine_decision_accepts_heterogeneous_traces() -> None:
    from samantha_server.engine.decision import (
        CanonicalizationTrace,
        EngineDecision,
        RefusalTrace,
    )

    traces = (
        CanonicalizationTrace(field="f", raw_value="r", canonical_value_attempted="c"),
        RefusalTrace(
            refusal_reason="STAGE_PRE_SKILL_UNAVAILABLE",
            refusal_stage="PRE",
            judge_verdict=None,
        ),
    )
    d = EngineDecision(
        applied_rule_id=None,
        next_state="ACCESSIONING",
        flags_added=(),
        flags_cleared=(),
        outcome="dispatch_empty",
        also_matched=(),
        dispatched_rule_ids=(),
        event_input_hash="a" * 64,
        primitive_traces={},
        latency_us=0,
        decision_traces=traces,
    )
    assert len(d.decision_traces) == 2
    assert d.decision_traces[0].kind == "canonicalization_warning"
    assert d.decision_traces[1].kind == "refusal"


def test_engine_decision_decision_traces_round_trips_json() -> None:
    """decision_traces serializes and deserializes faithfully via model_dump_json."""
    from samantha_server.engine.decision import (
        CanonicalizationTrace,
        EngineDecision,
        QueryTrace,
        RefusalTrace,
    )

    traces = (
        CanonicalizationTrace(
            field="patient_name",
            raw_value=" foo ",
            canonical_value_attempted="foo",
        ),
        QueryTrace(
            query_text_hash="h1",
            skill_doc_hash="h2",
            scenarios_cited=("SC-001",),
            model_id="llama-3b",
            response_text_hash="h3",
        ),
        RefusalTrace(
            refusal_reason="STAGE_PRE_PHI_BOUNDARY",
            refusal_stage="PRE",
            judge_verdict=None,
        ),
    )
    d = EngineDecision(
        applied_rule_id="ACC-001",
        next_state="HOLD",
        flags_added=(),
        flags_cleared=(),
        outcome="hold_missing_name",
        also_matched=(),
        dispatched_rule_ids=("ACC-001",),
        event_input_hash="a" * 64,
        primitive_traces={},
        latency_us=42,
        decision_traces=traces,
    )
    json_str = d.model_dump_json()
    d2 = EngineDecision.model_validate_json(json_str)

    assert len(d2.decision_traces) == 3
    assert d2.decision_traces[0].kind == "canonicalization_warning"
    assert d2.decision_traces[1].kind == "query"
    assert d2.decision_traces[2].kind == "refusal"
    assert d2.decision_traces[2].refusal_reason == "STAGE_PRE_PHI_BOUNDARY"  # type: ignore[union-attr]
    assert d2.decision_traces[2].refusal_stage == "PRE"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# _REFUSAL_REASONS Literal narrowing — prune dead Stage A/B codes
# ---------------------------------------------------------------------------

_EXPECTED_REFUSAL_REASONS = {
    "STAGE_PRE_SKILL_UNAVAILABLE",
    "STAGE_PRE_LLM_UNAVAILABLE",
    "STAGE_PRE_UNPARSEABLE",
    "STAGE_PRE_PHI_BOUNDARY",
}

_PRUNED_REFUSAL_REASONS = {
    "STAGE_A_NO_SKILL",
    "STAGE_B_UNGROUNDED",
    "STAGE_B_UNCERTAIN",
    "STAGE_B_LLM_UNAVAILABLE",
    "STAGE_B_UNPARSEABLE",
    "STAGE_PRE_NO_SKILL_FOR_STATE",
    "STAGE_PRE_NOT_IMPLEMENTED",
    "STAGE_PERSIST_UNAVAILABLE",
}


def test_refusal_reasons_literal_is_exactly_four_kept_codes() -> None:
    """(a): _REFUSAL_REASONS Literal contains exactly the 4 kept codes."""
    from samantha_server.engine.decision import _REFUSAL_REASONS  # type: ignore[attr-defined]

    actual = set(typing.get_args(_REFUSAL_REASONS))
    assert actual == _EXPECTED_REFUSAL_REASONS


@pytest.mark.parametrize("pruned_code", sorted(_PRUNED_REFUSAL_REASONS))
def test_pruned_refusal_reason_raises_validation_error(pruned_code: str) -> None:
    """(b/c): each pruned code raises ValidationError on RefusalTrace construction.

    The narrowed Literal rejects the value at field validation (before the stage
    invariant runs), so a uniform refusal_stage="PRE" construction triggers the
    error for all 8 pruned codes. Absence from the Literal is already guaranteed
    by test_refusal_reasons_literal_is_exactly_four_kept_codes (exact-set
    equality), so a separate membership-absence test would be redundant.
    """
    from pydantic import ValidationError

    from samantha_server.engine.decision import RefusalTrace

    with pytest.raises(ValidationError):
        RefusalTrace(
            refusal_reason=pruned_code,  # type: ignore[arg-type]
            refusal_stage="PRE",
            judge_verdict=None,
        )


def test_kept_stage_pre_codes_construct_successfully() -> None:
    """(d): all 4 live STAGE_PRE_* codes construct RefusalTrace successfully."""
    from samantha_server.engine.decision import RefusalTrace

    kept_pre_codes = [
        "STAGE_PRE_SKILL_UNAVAILABLE",
        "STAGE_PRE_LLM_UNAVAILABLE",
        "STAGE_PRE_UNPARSEABLE",
        "STAGE_PRE_PHI_BOUNDARY",
    ]
    for code in kept_pre_codes:
        t = RefusalTrace(
            refusal_reason=code,  # type: ignore[arg-type]
            refusal_stage="PRE",
            judge_verdict=None,
        )
        assert t.refusal_reason == code
