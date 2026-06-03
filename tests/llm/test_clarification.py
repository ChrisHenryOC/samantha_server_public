"""Tests for samantha_server.llm.handlers::handle_clarification + parser helpers."""

from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import MagicMock, patch

import pytest

# samantha_server.llm.handlers transitively imports samantha_server.config, which
# rejects the test-sentinel signing key during collection (before pytest sets
# PYTEST_CURRENT_TEST). Defer those imports inside test functions, mirroring
# the convention in tests/llm/test_handlers.py.
from samantha_server.engine.decision import (
    ClarificationTrace,
    RefusalTrace,
    compute_event_input_hash,
)
from samantha_server.errors import LLMInferenceError, PHIBoundaryError
from samantha_server.llm.preflight import PreflightMissing
from samantha_server.models.context import Event, Order, SpecimenContext


def _h() -> object:
    """Lazy accessor for samantha_server.llm.handlers.

    handlers transitively imports samantha_server.config, which rejects the
    test-sentinel signing key during *collection* (before pytest sets
    PYTEST_CURRENT_TEST). Calling _h() inside a test defers the import until
    the test is running, matching the convention in tests/llm/test_handlers.py.
    """
    from samantha_server.llm import handlers as _handlers

    return _handlers


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ctx(
    *,
    patient_name: str | None = None,
    fixative: str = "formalin",
    age: int = 45,
    current_state: str = "ACCESSIONING",
) -> SpecimenContext:
    return SpecimenContext(
        order=Order(
            order_id="CLARIF-001",
            patient_name=patient_name,
            patient_sex="F",
            age=age,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative=fixative,
            fixation_time_hours=24.0,
            ordered_tests=("ER",),
            priority="routine",
            billing_info_present=True,
        ),
        current_state=current_state,
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )


def _make_mock_llm(text: str = "fixative=formalin", model_id: str = "test-model") -> MagicMock:
    from samantha_server.llm.client import LLMResponse

    mock = MagicMock()
    mock.model_id = model_id
    mock.complete.return_value = LLMResponse(
        text=text,
        input_tokens=10,
        output_tokens=5,
        model_id=model_id,
        latency_us=1500,
    )
    return mock


def _missing_fixative() -> PreflightMissing:
    return PreflightMissing(unknown_canonical_fields=("fixative",))


# ---------------------------------------------------------------------------
# Slice 5: handle_clarification — happy path
# ---------------------------------------------------------------------------


def test_handle_clarification_outcome_is_needs_clarification() -> None:
    """handle_clarification returns outcome='needs_clarification'."""
    decision = _h().handle_clarification(
        _make_ctx(fixative="ethanol"),
        preflight_result=_missing_fixative(),
        llm_client=_make_mock_llm("fixative=formalin"),
    )
    assert decision.outcome == "needs_clarification"


def test_handle_clarification_no_state_transition() -> None:
    """handle_clarification keeps next_state == ctx.current_state."""
    decision = _h().handle_clarification(
        _make_ctx(fixative="ethanol", current_state="ACCESSIONING"),
        preflight_result=_missing_fixative(),
        llm_client=_make_mock_llm("fixative=formalin"),
    )
    assert decision.next_state == "ACCESSIONING"
    assert decision.applied_rule_id is None


def test_handle_clarification_emits_event_input_hash() -> None:
    """H-02: decision.event_input_hash matches compute_event_input_hash(ctx)."""
    ctx = _make_ctx(fixative="ethanol")
    decision = _h().handle_clarification(
        ctx,
        preflight_result=_missing_fixative(),
        llm_client=_make_mock_llm("fixative=formalin"),
    )
    assert decision.event_input_hash == compute_event_input_hash(ctx)


def test_handle_clarification_event_input_hash_is_deterministic() -> None:
    """Same input ctx → same event_input_hash on every call (receipt replay determinism)."""
    ctx = _make_ctx(fixative="ethanol")
    d1 = _h().handle_clarification(
        ctx,
        preflight_result=_missing_fixative(),
        llm_client=_make_mock_llm("fixative=formalin"),
    )
    d2 = _h().handle_clarification(
        ctx,
        preflight_result=_missing_fixative(),
        llm_client=_make_mock_llm("fixative=formalin"),
    )
    assert d1.event_input_hash == d2.event_input_hash


def test_handle_clarification_trace_records_separate_field_categories() -> None:
    """ClarificationTrace stores missing_fields and unknown_canonical_fields separately."""
    decision = _h().handle_clarification(
        _make_ctx(fixative="ethanol", age=None),
        preflight_result=PreflightMissing(
            missing_fields=("age",),
            unknown_canonical_fields=("fixative",),
        ),
        llm_client=_make_mock_llm("fixative=formalin"),
    )
    trace = decision.decision_traces[0]
    assert isinstance(trace, ClarificationTrace)
    assert trace.missing_fields == ("age",)
    assert trace.unknown_canonical_fields == ("fixative",)


def test_handle_clarification_records_model_id() -> None:
    """ClarificationTrace.model_id carries the LLM's model_id on success."""
    decision = _h().handle_clarification(
        _make_ctx(fixative="ethanol"),
        preflight_result=_missing_fixative(),
        llm_client=_make_mock_llm("fixative=formalin", model_id="qwen-3b-test"),
    )
    trace = decision.decision_traces[0]
    assert isinstance(trace, ClarificationTrace)
    assert trace.model_id == "qwen-3b-test"


def test_handle_clarification_suggested_values_are_canonicalized() -> None:
    """suggested_values reflect the canonical pick-list form (re-canonicalized)."""
    decision = _h().handle_clarification(
        _make_ctx(fixative="ethanol"),
        preflight_result=_missing_fixative(),
        # LLM returns mixed case; canonicalize() lower-cases.
        llm_client=_make_mock_llm("fixative=Formalin"),
    )
    trace = decision.decision_traces[0]
    assert isinstance(trace, ClarificationTrace)
    assert trace.suggested_values == {"fixative": "formalin"}


def test_handle_clarification_drops_unknown_pick_list_value() -> None:
    """suggested_values drops values that aren't in the canonical pick list."""
    decision = _h().handle_clarification(
        _make_ctx(fixative="ethanol"),
        preflight_result=_missing_fixative(),
        llm_client=_make_mock_llm("fixative=glycerin"),  # not in pick list
    )
    trace = decision.decision_traces[0]
    assert isinstance(trace, ClarificationTrace)
    assert trace.suggested_values == {}


def test_handle_clarification_unrecognized_lines_dropped() -> None:
    """Lines without a recognized field are silently dropped."""
    decision = _h().handle_clarification(
        _make_ctx(fixative="ethanol"),
        preflight_result=_missing_fixative(),
        llm_client=_make_mock_llm("fixative=formalin\nrandom text\nanother line"),
    )
    trace = decision.decision_traces[0]
    assert isinstance(trace, ClarificationTrace)
    assert trace.suggested_values == {"fixative": "formalin"}


def test_handle_clarification_latency_from_response() -> None:
    """handle_clarification carries response.latency_us on success."""
    decision = _h().handle_clarification(
        _make_ctx(fixative="ethanol"),
        preflight_result=_missing_fixative(),
        llm_client=_make_mock_llm("fixative=formalin"),
    )
    assert decision.latency_us == 1500


def test_handle_clarification_calls_llm_temperature_zero() -> None:
    """Determinism: handle_clarification passes temperature=0.0 to the LLM."""
    llm = _make_mock_llm("fixative=formalin")
    _h().handle_clarification(
        _make_ctx(fixative="ethanol"),
        preflight_result=_missing_fixative(),
        llm_client=llm,
    )
    _, kwargs = llm.complete.call_args
    assert kwargs.get("temperature") == 0.0


def test_handle_clarification_prompt_lists_fields() -> None:
    """The prompt lists every field in the union of missing/unknown."""
    llm = _make_mock_llm("fixative=formalin\nage=45")
    _h().handle_clarification(
        _make_ctx(fixative="ethanol", age=None),
        preflight_result=PreflightMissing(
            missing_fields=("age",), unknown_canonical_fields=("fixative",)
        ),
        llm_client=llm,
    )
    prompt = llm.complete.call_args[0][0]
    assert "fixative" in prompt
    assert "age" in prompt


# ---------------------------------------------------------------------------
# H-03: PHI exclusion — prompt MUST NOT contain raw PHI
# ---------------------------------------------------------------------------


def test_handle_clarification_prompt_excludes_patient_name() -> None:
    """Prompt passed to LLM does not contain raw patient_name."""
    llm = _make_mock_llm("fixative=formalin")
    _h().handle_clarification(
        _make_ctx(patient_name="Jane Doe", fixative="ethanol"),
        preflight_result=_missing_fixative(),
        llm_client=llm,
    )
    prompt = llm.complete.call_args[0][0]
    assert "Jane Doe" not in prompt
    assert "Jane" not in prompt
    assert "Doe" not in prompt


# ---------------------------------------------------------------------------
# Slice 6: LLM-error fallback
# ---------------------------------------------------------------------------


def test_handle_clarification_llm_error_still_emits_clarification() -> None:
    """LLMClientError → clarification still emits, with empty suggestions."""
    mock = MagicMock()
    mock.model_id = "test-model"
    mock.complete.side_effect = LLMInferenceError(model_id="test-model", cause="GPU OOM")

    ctx = _make_ctx(fixative="ethanol")
    decision = _h().handle_clarification(
        ctx,
        preflight_result=_missing_fixative(),
        llm_client=mock,
    )
    assert decision.outcome == "needs_clarification"
    trace = decision.decision_traces[0]
    assert isinstance(trace, ClarificationTrace)
    assert trace.suggested_values == {}
    assert trace.model_id == ""
    assert decision.latency_us == 0
    assert decision.order_id == ctx.order.order_id


def test_handle_clarification_llm_error_missing_fields_preserved() -> None:
    """LLMClientError preserves the original missing/unknown field tuples."""
    mock = MagicMock()
    mock.model_id = "test-model"
    mock.complete.side_effect = LLMInferenceError(model_id="test-model", cause="GPU OOM")

    decision = _h().handle_clarification(
        _make_ctx(fixative="ethanol", age=None),
        preflight_result=PreflightMissing(
            missing_fields=("age",), unknown_canonical_fields=("fixative",)
        ),
        llm_client=mock,
    )
    trace = decision.decision_traces[0]
    assert isinstance(trace, ClarificationTrace)
    assert trace.missing_fields == ("age",)
    assert trace.unknown_canonical_fields == ("fixative",)


# ---------------------------------------------------------------------------
# PHI boundary refusal
# ---------------------------------------------------------------------------


def test_handle_clarification_phi_boundary_error_emits_refusal() -> None:
    """PHIBoundaryError → STAGE_PRE_PHI_BOUNDARY refusal."""
    ctx = _make_ctx(fixative="ethanol")
    with patch("samantha_server.llm.handlers.phi_safe", side_effect=PHIBoundaryError(age=92)):
        decision = _h().handle_clarification(
            ctx,
            preflight_result=_missing_fixative(),
            llm_client=_make_mock_llm(),
        )
    assert decision.outcome == "refused_phi_boundary"
    trace = decision.decision_traces[0]
    assert isinstance(trace, RefusalTrace)
    assert trace.refusal_reason == "STAGE_PRE_PHI_BOUNDARY"


# ---------------------------------------------------------------------------
# M-05: parser unit tests — duplicate field, value-with-=, length cap
# ---------------------------------------------------------------------------


def test_parse_first_write_wins_on_duplicate_field() -> None:
    """Duplicate field lines: first write wins (later lines ignored)."""
    result = _h()._parse_clarification_response(
        "fixative=formalin\nfixative=alcohol",
        ("fixative",),
    )
    assert result == {"fixative": "formalin"}


def test_parse_value_with_equals_sign() -> None:
    """A value containing '=' is preserved (partition splits on first '=' only)."""
    # Use a non-canonical field where canonicalize() passes the value through.
    # 'order_id' is not in CANONICAL_FIELDS, so was_unknown is always False.
    result = _h()._parse_clarification_response(
        "order_id=ABC=123",
        ("order_id",),
    )
    assert result == {"order_id": "abc=123"}


def test_parse_value_length_capped() -> None:
    """Values longer than _h()._MAX_SUGGESTED_VALUE_LEN are truncated."""
    long_value = "x" * (_h()._MAX_SUGGESTED_VALUE_LEN + 50)
    result = _h()._parse_clarification_response(
        f"order_id={long_value}",
        ("order_id",),
    )
    assert len(result["order_id"]) == _h()._MAX_SUGGESTED_VALUE_LEN


def test_parse_drops_field_not_in_set() -> None:
    """Lines whose field is outside fields_to_clarify are dropped."""
    result = _h()._parse_clarification_response(
        "fixative=formalin\npriority=routine",
        ("fixative",),  # priority not in set
    )
    assert result == {"fixative": "formalin"}


def test_parse_empty_text_returns_empty_dict() -> None:
    """Empty text yields empty dict (no exception)."""
    assert _h()._parse_clarification_response("", ("fixative",)) == {}


@pytest.mark.parametrize(
    "text",
    [
        "no equals sign here",
        "= no field name",
        "   ",
        "\n\n\n",
    ],
)
def test_parse_malformed_text_returns_empty_dict(text: str) -> None:
    """Malformed text yields empty dict."""
    result = _h()._parse_clarification_response(text, ("fixative",))
    assert result == {}


# ---------------------------------------------------------------------------
# GH-214: prompt must carry the canonical pick lists for each clarified field
# ---------------------------------------------------------------------------


def test_build_clarification_prompt_includes_canonical_values_block() -> None:
    """GH-214: prompt must contain a `<canonical_values>` scaffolding block.

    Without the block, the model is asked to map raw values to canonical forms
    with no list of what counts as canonical — it has to guess, and the post-
    hoc canonicalize() filter drops non-canonical suggestions. Wastes an LLM
    call per clarification.
    """
    from samantha_server.llm.phi import phi_safe

    safe_ctx = phi_safe(_make_ctx(fixative="ethanol"))
    pick_lists = {"fixative": frozenset({"formalin", "ethanol", "rpmi"})}
    prompt = _h().build_clarification_prompt(safe_ctx, ("fixative",), pick_lists)
    assert "<canonical_values>" in prompt
    assert "</canonical_values>" in prompt
    # Per-field rendering: the field name and each canonical value must appear
    # inside the block.
    cv_start = prompt.index("<canonical_values>")
    cv_end = prompt.index("</canonical_values>")
    cv_block = prompt[cv_start:cv_end]
    assert "fixative" in cv_block
    for value in ("formalin", "ethanol", "rpmi"):
        assert value in cv_block, f"canonical value {value!r} missing from block"


def test_build_clarification_prompt_truncates_long_pick_lists() -> None:
    """Pick lists with > 20 values are capped with a `...+N more` suffix.

    Bounded rendering keeps prompt token-cost predictable when the pick list
    grows (e.g., anatomic_site enumerations).
    """
    from samantha_server.llm.phi import phi_safe

    safe_ctx = phi_safe(_make_ctx())
    big = frozenset(f"val_{i:03d}" for i in range(25))
    prompt = _h().build_clarification_prompt(safe_ctx, ("anatomic_site",), {"anatomic_site": big})
    # First 20 sorted values are rendered; remainder is summarized.
    assert "...+5 more" in prompt
    # PR #268 review #2: pin the sorted-determinism contract — the first 20
    # values must appear and the last 5 must not. Without this, a regression
    # that drops the sorted() call still passes the `...+5 more` count check.
    cv_start = prompt.index("<canonical_values>")
    cv_end = prompt.index("</canonical_values>")
    cv_block = prompt[cv_start:cv_end]
    for i in range(20):
        assert f"val_{i:03d}" in cv_block, f"sorted top-20 value val_{i:03d} missing"
    for i in range(20, 25):
        assert f"val_{i:03d}" not in cv_block, (
            f"truncated value val_{i:03d} leaked into rendered block"
        )


def test_build_clarification_prompt_instruction_references_pick_list() -> None:
    """Instruction prose must point the model at the `<canonical_values>` block.

    The pre-GH-214 instruction said only 'provide the canonical form of the
    value', which is under-specified once the block exists. Update it to tell
    the model to pick from the listed values and omit fields it cannot match.
    """
    from samantha_server.llm.phi import phi_safe

    safe_ctx = phi_safe(_make_ctx(fixative="ethanol"))
    pick_lists = {"fixative": frozenset({"formalin", "ethanol"})}
    prompt = _h().build_clarification_prompt(safe_ctx, ("fixative",), pick_lists)
    lower = prompt.lower()
    assert "canonical_values" in lower, (
        "instruction must reference the `<canonical_values>` block by name"
    )
    assert "omit" in lower, (
        "instruction must tell the model to omit fields it cannot map "
        "(prevents the model guessing past the pick list)"
    )


def test_build_clarification_prompt_skips_fields_without_pick_list() -> None:
    """Fields with no entry in pick_lists (e.g., free-text age) are omitted from the block.

    Renders the block only for fields that have a canonical list — for other
    fields, the model would still see them in `<fields_to_clarify>` but with
    no constraint, which is the pre-GH-214 behavior (acceptable since those
    fields don't go through canonicalize()).
    """
    from samantha_server.llm.phi import phi_safe

    safe_ctx = phi_safe(_make_ctx(fixative="ethanol", age=None))
    pick_lists = {"fixative": frozenset({"formalin", "ethanol"})}
    prompt = _h().build_clarification_prompt(safe_ctx, ("age", "fixative"), pick_lists)
    cv_start = prompt.index("<canonical_values>")
    cv_end = prompt.index("</canonical_values>")
    cv_block = prompt[cv_start:cv_end]
    assert "fixative" in cv_block
    # `age` is in fields_to_clarify but has no pick list — must not appear
    # inside the canonical_values block.
    age_lines = [line for line in cv_block.splitlines() if "age" in line]
    assert not age_lines, (
        f"age has no pick_list entry but appears in canonical_values: {age_lines!r}"
    )
    # PR #268 review #5: pin the combined contract — `age` must still appear
    # in <fields_to_clarify> so the model knows it needs clarification, even
    # though it has no canonical-form constraint.
    ftc_start = prompt.index("<fields_to_clarify>")
    ftc_end = prompt.index("</fields_to_clarify>")
    ftc_block = prompt[ftc_start:ftc_end]
    assert "age" in ftc_block, (
        "age must still appear in <fields_to_clarify> even when omitted from <canonical_values>"
    )


def test_build_clarification_prompt_omits_block_when_all_fields_free_text() -> None:
    """PR #268 review #1: all-free-text fields → no `<canonical_values>` block at all.

    Previously the block was always emitted; when no field had a pick list
    the result was `<canonical_values>\\n\\n</canonical_values>` — an empty
    element directly contradicting the instruction prose that tells the
    model to "choose from the corresponding pick list shown in
    <canonical_values>". Omit the block entirely instead.
    """
    from samantha_server.llm.phi import phi_safe

    safe_ctx = phi_safe(_make_ctx(age=None))
    prompt = _h().build_clarification_prompt(safe_ctx, ("age",), {})
    assert "<canonical_values>" not in prompt, (
        "block must be omitted entirely when no field has a pick list "
        "(prevents the empty-element / instruction-prose contradiction)"
    )
    # Prompt structure must still be coherent — no double-blank gap where the
    # block would have sat.
    assert "\n\n\n" not in prompt, "filtered-empty-block must not leave a triple-newline gap"


def test_build_clarification_prompt_renders_empty_pick_list_explicitly() -> None:
    """PR #268 review #4: a field present in pick_lists with an empty frozenset must render.

    Distinguishes 'field absent from pick_lists' (intentional — free-text)
    from 'field present with empty list' (data-file authoring error). The
    empty case must be visible in the prompt so an operator inspecting it
    can spot the authoring mistake, rather than silently degrading to the
    same shape as a missing entry.
    """
    from samantha_server.llm.phi import phi_safe

    safe_ctx = phi_safe(_make_ctx(fixative="ethanol"))
    pick_lists: Mapping[str, frozenset[str]] = {"fixative": frozenset()}
    prompt = _h().build_clarification_prompt(safe_ctx, ("fixative",), pick_lists)
    # The field name must appear inside the block, with no values — surfaces
    # the authoring error rather than masking it.
    assert "<canonical_values>" in prompt, (
        "field with empty pick list must still emit the block (visible authoring error)"
    )
    cv_start = prompt.index("<canonical_values>")
    cv_end = prompt.index("</canonical_values>")
    cv_block = prompt[cv_start:cv_end]
    assert "fixative" in cv_block


def test_handle_clarification_prompt_carries_canonical_values_block() -> None:
    """End-to-end: `handle_clarification` plumbs `PICK_LISTS` into the prompt.

    Without this wiring the refactored signature would be useless — the prompt
    sent to the LLM would still be the pre-GH-214 shape.
    """
    llm = _make_mock_llm("fixative=formalin")
    _h().handle_clarification(
        _make_ctx(fixative="ethanol"),
        preflight_result=_missing_fixative(),
        llm_client=llm,
    )
    prompt = llm.complete.call_args[0][0]
    assert "<canonical_values>" in prompt
    # Sanity: the real pick list for `fixative` includes 'formalin' as a canonical value.
    cv_start = prompt.index("<canonical_values>")
    cv_end = prompt.index("</canonical_values>")
    assert "formalin" in prompt[cv_start:cv_end]


# ---------------------------------------------------------------------------
# GH-369 Slice 3: handle_clarification stamps ctx.order.order_id
# ---------------------------------------------------------------------------


def test_handle_clarification_success_carries_order_id() -> None:
    """GH-369: success path stamps ctx.order.order_id on the decision."""
    ctx = _make_ctx(fixative="ethanol")
    decision = _h().handle_clarification(
        ctx,
        preflight_result=_missing_fixative(),
        llm_client=_make_mock_llm("fixative=formalin"),
    )
    assert decision.order_id == ctx.order.order_id


def test_handle_clarification_phi_boundary_refusal_carries_order_id() -> None:
    """GH-369: PHIBoundaryError refusal stamps ctx.order.order_id on the decision."""
    ctx = _make_ctx(fixative="ethanol")
    with patch("samantha_server.llm.handlers.phi_safe", side_effect=PHIBoundaryError(age=92)):
        decision = _h().handle_clarification(
            ctx,
            preflight_result=_missing_fixative(),
            llm_client=_make_mock_llm(),
        )
    assert decision.order_id == ctx.order.order_id
