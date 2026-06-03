"""Architectural invariant tests for the engine kernel.

These tests verify:
- The dispatch boundary (UndispatchedRuleError) is raised when rules/token are forged
- ACC severity hierarchy with real corpus specs
- First-match by priority for non-ACC steps
- IHC applies_at dispatch
- No LLM imports in the engine package
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from samantha_server.engine.dispatcher import DispatchResult, list_applicable_rules
from samantha_server.engine.evaluator import UndispatchedRuleError, evaluate
from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.rules.loader import RuleIndex, load_rule_specs

SPECS_DIR = Path(__file__).parent.parent.parent / "samantha_server" / "rules" / "specs"


@pytest.fixture(scope="module")
def rule_index() -> RuleIndex:
    specs = load_rule_specs(SPECS_DIR)
    return RuleIndex(specs)


def _make_order(**overrides: object) -> Order:
    defaults = dict(
        order_id="O-001",
        patient_name="Jane Doe",
        patient_sex="F",
        age=45,
        specimen_type="biopsy",
        anatomic_site="breast",
        fixative="formalin",
        fixation_time_hours=8.0,
        ordered_tests=("HER2",),
        priority="ROUTINE",
        billing_info_present=True,
    )
    defaults.update(overrides)
    return Order(**defaults)  # type: ignore[arg-type]


def _make_ctx(
    state: str = "ACCESSIONING",
    event_type: str = "order_received",
    event_data: dict | None = None,
    flags: frozenset[str] | None = None,
    **order_overrides: object,
) -> SpecimenContext:
    return SpecimenContext(
        order=_make_order(**order_overrides),
        current_state=state,
        flags=flags or frozenset(),
        event=Event(
            event_type=event_type,
            event_data=event_data or {},
            step_index=0,
        ),
    )


class TestNoLLMImportsInEngine:
    """The engine package must not import any LLM-related modules."""

    _LLM_MODULE_PREFIXES = (
        "openai",
        "anthropic",
        "langchain",
        "litellm",
        "google.generativeai",
        "cohere",
        "huggingface_hub",
        "transformers",
    )

    def _get_engine_modules(self) -> list[str]:
        engine_pkg = Path(__file__).parent.parent.parent / "samantha_server" / "engine"
        modules = []
        for py_file in engine_pkg.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            module_name = f"samantha_server.engine.{py_file.stem}"
            modules.append(module_name)
        return modules

    def test_no_llm_imports(self) -> None:
        engine_modules = self._get_engine_modules()
        assert engine_modules, "No engine modules found — check package path"

        for module_name in engine_modules:
            mod = importlib.import_module(module_name)
            mod_file = getattr(mod, "__file__", "") or ""
            source = Path(mod_file).read_text() if mod_file else ""
            for prefix in self._LLM_MODULE_PREFIXES:
                assert f"import {prefix}" not in source, (
                    f"{module_name} imports LLM module '{prefix}'"
                )
                assert f"from {prefix}" not in source, (
                    f"{module_name} imports from LLM module '{prefix}'"
                )


class TestDispatchBoundary:
    """evaluate() refuses DispatchResults not produced by list_applicable_rules."""

    def test_tampered_rules_raises_undispatched(self, rule_index: RuleIndex) -> None:
        """A DispatchResult with modified rules (wrong HMAC) raises."""
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        # Fabricate a DispatchResult with tampered rules but the real token
        tampered_rules = (
            dispatch.rules[1:] if len(dispatch.rules) > 1 else dispatch.rules + dispatch.rules
        )
        tampered_dispatch = DispatchResult(
            rules=tampered_rules, token=dispatch.token, session_id="test"
        )
        with pytest.raises(UndispatchedRuleError):
            evaluate(tampered_dispatch, ctx)

    def test_forged_token_raises_undispatched(self, rule_index: RuleIndex) -> None:
        """A DispatchResult with a bitflipped token raises."""
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        forged_token = bytes(b ^ 0xFF for b in dispatch.token)
        forged_dispatch = DispatchResult(
            rules=dispatch.rules, token=forged_token, session_id="test"
        )
        with pytest.raises(UndispatchedRuleError):
            evaluate(forged_dispatch, ctx)

    def test_tokens_differ_per_call(self, rule_index: RuleIndex) -> None:
        """Each call to list_applicable_rules produces a distinct token (different nonce)."""
        ctx = _make_ctx()
        dispatch1 = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        dispatch2 = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        # Per-call nonces ensure token uniqueness even for identical rule sets
        assert dispatch1.token != dispatch2.token
        # Both tokens are valid for the same ctx+rules
        evaluate(dispatch1, ctx)
        evaluate(dispatch2, ctx)

    def test_fabricated_dispatch_result_raises_undispatched(self, rule_index: RuleIndex) -> None:
        """T4: A fully fabricated DispatchResult with a zeroed token must be rejected
        even when the rules field looks plausible.  This verifies that the HMAC binding
        cannot be forged by constructing a DispatchResult directly."""
        ctx = _make_ctx()
        # Build a real dispatch so we can steal its rules tuple
        real_dispatch = list_applicable_rules(
            ctx, rule_index, session_id="test-session", ttl_sec=60
        )
        fabricated = DispatchResult(
            rules=real_dispatch.rules, token=b"\x00" * 64, session_id="test"
        )
        with pytest.raises(UndispatchedRuleError):
            evaluate(fabricated, ctx)


class TestAccSeverityHierarchy:
    """REJECT beats HOLD beats PROCEED; ACCEPT only when nothing else matches."""

    def test_reject_beats_hold(self, rule_index: RuleIndex) -> None:
        """ACC-001 (HOLD: missing patient_name) and ACC-005 (REJECT: HER2 non-formalin)
        both match. REJECT wins."""
        ctx = _make_ctx(
            patient_name=None,  # ACC-001 HOLD
            fixative="ethanol",  # ACC-005 REJECT (HER2 + non-formalin)
            ordered_tests=("HER2",),
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert decision.applied_rule_id == "ACC-005"
        assert "ACC-001" in decision.also_matched

    def test_acc008_accept_only_when_all_others_fail(self, rule_index: RuleIndex) -> None:
        """ACC-008 fires only when no other ACC rule matches (all-validations-pass)."""
        ctx = _make_ctx(
            patient_name="Jane Doe",
            patient_sex="F",
            age=45,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=8.0,
            ordered_tests=("HER2",),
            billing_info_present=True,
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert decision.applied_rule_id == "ACC-008"
        assert decision.next_state == "ACCEPTED"
        assert decision.outcome == "accessioning_validations_passed"

    def test_acc008_not_applied_when_hold_present(self, rule_index: RuleIndex) -> None:
        """ACC-008 predicate is NOT(OR(ACC-001..007,ACC-009)) — won't fire when others match."""
        ctx = _make_ctx(patient_name=None)  # ACC-001 HOLD fires
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert decision.applied_rule_id != "ACC-008"


class TestFirstMatchByPriority:
    """Non-ACCESSIONING steps: first rule in priority order that matches fires."""

    def test_sp001_fires_at_processing_complete(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx(
            state="SAMPLE_PREP_PROCESSING",
            event_type="processing_complete",
            event_data={"outcome": "success"},
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert decision.applied_rule_id == "SP-001"
        assert decision.also_matched == ()

    def test_sp004_fires_at_sample_prep_qc(self, rule_index: RuleIndex) -> None:
        """T1: SP-004 fires at SAMPLE_PREP_QC with sample_prep_qc/pass event."""
        ctx = _make_ctx(
            state="SAMPLE_PREP_QC",
            event_type="sample_prep_qc",
            event_data={"outcome": "pass"},
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert decision.applied_rule_id == "SP-004"
        assert decision.also_matched == ()


class TestIHCDispatch:
    """IHC dispatch uses applies_at (not step) to select rules."""

    def test_ihc001_dispatched_at_ihc_staining(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx(
            state="IHC_STAINING",
            event_type="ihc_staining_complete",
            event_data={
                "her2_added_at_review": True,
            },
            fixative="formalin",
            fixation_time_hours=1.0,  # below 6h → ACC fails, but here we're at IHC
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        dispatched_ids = {r.rule_id for r in dispatch.rules}
        assert "IHC-001" in dispatched_ids
        assert "IHC-002" not in dispatched_ids

    def test_ihc002_dispatched_at_ihc_qc(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx(
            state="IHC_QC",
            event_type="ihc_qc",
            event_data={"all_slides_complete": True},
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        dispatched_ids = {r.rule_id for r in dispatch.rules}
        assert "IHC-002" in dispatched_ids
        assert "IHC-001" not in dispatched_ids

    def test_ihc001_fires_when_her2_fixation_rejected(self, rule_index: RuleIndex) -> None:
        """IHC-001 fires when HER2 was added at review AND fixation is non-compliant."""
        ctx = _make_ctx(
            state="IHC_STAINING",
            event_type="ihc_staining_complete",
            event_data={"her2_added_at_review": True},
            fixative="formalin",
            fixation_time_hours=1.0,  # below 6h threshold → IHC-001 fires
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert decision.applied_rule_id == "IHC-001"
        assert decision.next_state == "IHC_STAINING"

    def test_ihc002_fires_when_all_slides_complete(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx(
            state="IHC_QC",
            event_type="ihc_qc",
            event_data={"all_slides_complete": True},
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert decision.applied_rule_id == "IHC-002"
        assert decision.next_state == "IHC_SCORING"

    def test_ihc_mismatched_event_returns_dispatch_empty(self, rule_index: RuleIndex) -> None:
        """T5: At IHC_STAINING with wrong event type, no rules fire → dispatch_empty."""
        # wrong event for IHC_STAINING — IHC-001 needs ihc_staining_complete
        ctx = _make_ctx(
            state="IHC_STAINING",
            event_type="ihc_qc",
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert decision.applied_rule_id is None
        assert decision.outcome == "dispatch_empty"


class TestEvaluateAlwaysReturnsDecision:
    """Every decision path returns an EngineDecision — no silent None."""

    def test_empty_dispatch_returns_decision(self, rule_index: RuleIndex) -> None:
        from samantha_server.engine.decision import EngineDecision

        ctx = _make_ctx(state="MISSING_INFO_HOLD", event_type="noop")
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        result = evaluate(dispatch, ctx)
        assert isinstance(result, EngineDecision)
        assert result is not None

    def test_no_match_returns_dispatch_empty(self, rule_index: RuleIndex) -> None:
        """T6: No-match decision has the correct outcome, applied_rule_id, and next_state.

        Uses an event type that no SP rule listens on at SAMPLE_PREP_PROCESSING
        to guarantee no rules fire.
        """
        from samantha_server.engine.decision import EngineDecision

        ctx = _make_ctx(
            state="SAMPLE_PREP_PROCESSING",
            event_type="unrecognised_event_xyz",
            event_data={"outcome": "failure"},
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        result = evaluate(dispatch, ctx)
        assert isinstance(result, EngineDecision)
        assert result.outcome == "dispatch_empty"
        assert result.applied_rule_id is None
        assert result.next_state == ctx.current_state


class TestResultingStepEvaluation:
    """T7: RESULTING-step rules fire correctly with flags-based context."""

    def test_res001_fires_when_missing_info_proceed_flag_set(self, rule_index: RuleIndex) -> None:
        """RES-001 fires when MISSING_INFO_PROCEED flag is set and event is resulting_review."""
        ctx = _make_ctx(
            state="RESULTING",
            event_type="resulting_review",
            flags=frozenset({"MISSING_INFO_PROCEED"}),
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert decision.applied_rule_id == "RES-001"
        assert decision.next_state == "RESULTING_HOLD"
