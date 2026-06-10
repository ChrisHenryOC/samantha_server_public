"""Tests for evaluator.evaluate and UndispatchedRuleError."""

import pytest

from samantha_server.engine.decision import EngineDecision
from samantha_server.engine.dispatcher import DispatchResult, list_applicable_rules
from samantha_server.engine.evaluator import UndispatchedRuleError, evaluate
from samantha_server.models.context import Event, Order, SpecimenContext
from samantha_server.rules.loader import RuleIndex, load_rule_specs

SPECS_DIR = (
    __import__("pathlib").Path(__file__).parent.parent.parent
    / "samantha_server"
    / "rules"
    / "specs"
)


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
        ordered_tests=("her2",),
        priority="routine",
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


class TestUndispatchedRuleError:
    def test_cross_session_replay_raises_undispatched_rule_error(
        self, rule_index: RuleIndex
    ) -> None:
        """evaluate() with a mismatched session_id raises UndispatchedRuleError.

        Regression guard for fix #3: evaluate must pass the
        *caller's* session_id to verify_dispatch, not dispatch.session_id.
        When dispatch was minted for session "A" but the caller presents
        session "B", verify_dispatch returns False and evaluate() must raise.
        """
        ctx = _make_ctx()
        # Issue a dispatch token bound to session "A".
        dispatch = list_applicable_rules(ctx, rule_index, session_id="session-A", ttl_sec=60)
        # Evaluating it with session "B" must fail — cross-session replay rejected.
        with pytest.raises(UndispatchedRuleError):
            evaluate(dispatch, ctx, session_id="session-B")

    def test_same_session_does_not_raise(self, rule_index: RuleIndex) -> None:
        """evaluate() with the matching session_id succeeds."""
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        # Should not raise — session matches.
        result = evaluate(dispatch, ctx, session_id="test-session")
        assert result is not None

    def test_wrong_token_raises(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        forged = DispatchResult(
            rules=dispatch.rules,
            token=bytes(b ^ 0xFF for b in dispatch.token),
            session_id="test-session",
        )
        with pytest.raises(UndispatchedRuleError):
            evaluate(forged, ctx, session_id="test-session")

    def test_wrong_rules_raises(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        # Pass a tampered DispatchResult with one rule removed but the original token
        tampered_rules = dispatch.rules[1:] if len(dispatch.rules) > 1 else ()
        tampered = DispatchResult(rules=tampered_rules, token=dispatch.token, session_id="test")
        with pytest.raises(UndispatchedRuleError):
            evaluate(tampered, ctx, session_id="test-session")

    def test_empty_dispatch_with_wrong_token_raises(self, rule_index: RuleIndex) -> None:
        """Even for an empty dispatch, token must be valid."""
        ctx = _make_ctx(state="MISSING_INFO_HOLD", event_type="some_event")
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        assert dispatch.rules == ()
        forged = DispatchResult(rules=dispatch.rules, token=b"\xff" * 64, session_id="test-session")
        with pytest.raises(UndispatchedRuleError):
            evaluate(forged, ctx, session_id="test-session")


class TestDispatchEmpty:
    def test_dispatch_empty_returns_engine_decision_with_no_rule(
        self, rule_index: RuleIndex
    ) -> None:
        """No matching rules → outcome='dispatch_empty', applied_rule_id=None."""
        ctx = _make_ctx(state="MISSING_INFO_HOLD", event_type="some_event")
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert isinstance(decision, EngineDecision)
        assert decision.applied_rule_id is None
        assert decision.next_state == ctx.current_state
        assert decision.outcome == "dispatch_empty"
        assert decision.also_matched == ()
        assert decision.dispatched_rule_ids == ()
        assert decision.primitive_traces == {}
        assert decision.latency_us >= 0


class TestAccessioningEvaluation:
    def test_acc008_accept_when_all_validations_pass(self, rule_index: RuleIndex) -> None:
        """All-validations-pass case: only ACC-008 fires → transition to ACCEPTED."""
        # A context with all required fields filled, fixation in range, formalin fixative
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

    def test_reject_beats_hold_severity_hierarchy(self, rule_index: RuleIndex) -> None:
        """When ACC-001 (HOLD: missing patient_name) and ACC-005 (REJECT: HER2 bad fixative)
        both match, REJECT wins."""
        ctx = _make_ctx(
            patient_name=None,  # triggers ACC-001 (HOLD)
            patient_sex="F",
            age=45,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="ethanol",  # triggers ACC-005 (REJECT: HER2 bad fixative)
            fixation_time_hours=8.0,
            ordered_tests=("HER2",),
            billing_info_present=True,
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        # ACC-005 is REJECT, should win over ACC-001 which is HOLD
        assert decision.applied_rule_id == "ACC-005"
        assert decision.next_state == "DO_NOT_PROCESS"

    def test_hold_only_case(self, rule_index: RuleIndex) -> None:
        """Only a HOLD rule matches — transition is to MISSING_INFO_HOLD."""
        ctx = _make_ctx(
            patient_name=None,  # triggers ACC-001 (HOLD)
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
        assert decision.applied_rule_id == "ACC-001"
        assert decision.next_state == "MISSING_INFO_HOLD"

    def test_also_matched_contains_non_applied_matches(self, rule_index: RuleIndex) -> None:
        """When multiple ACC rules match, also_matched lists the non-winning ones."""
        ctx = _make_ctx(
            patient_name=None,  # triggers ACC-001 (HOLD)
            patient_sex=None,  # triggers ACC-002 (HOLD)
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
        # Both ACC-001 and ACC-002 are HOLD — one applied, the other in also_matched
        all_ids = {decision.applied_rule_id} | set(decision.also_matched)
        assert "ACC-001" in all_ids
        assert "ACC-002" in all_ids

    def test_same_severity_tie_break_winner_is_acc001(self, rule_index: RuleIndex) -> None:
        """T2: When ACC-001 and ACC-002 both fire (both HOLD), ACC-001 wins because
        it loads first in sort-stable order (first-encountered wins on ties)."""
        ctx = _make_ctx(
            patient_name=None,  # triggers ACC-001 (HOLD)
            patient_sex=None,  # triggers ACC-002 (HOLD)
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
        assert decision.applied_rule_id == "ACC-001"
        assert "ACC-002" in decision.also_matched

    def test_dispatched_rule_ids_matches_dispatch_result(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert set(decision.dispatched_rule_ids) == {r.rule_id for r in dispatch.rules}

    def test_latency_us_is_positive_integer(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert isinstance(decision.latency_us, int)
        assert decision.latency_us >= 0

    def test_event_input_hash_is_deterministic(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx()
        dispatch1 = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        dispatch2 = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        d1 = evaluate(dispatch1, ctx)
        d2 = evaluate(dispatch2, ctx)
        assert d1.event_input_hash == d2.event_input_hash

    def test_primitive_traces_only_for_matched_rules(self, rule_index: RuleIndex) -> None:
        """primitive_traces contains entries only for applied + also_matched rules."""
        ctx = _make_ctx(
            patient_name=None,  # triggers ACC-001 (HOLD)
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
        # ACC-001 matched; its trace should be present
        assert "ACC-001" in decision.primitive_traces
        # Unmatched rules should not appear in traces
        matched_ids = {decision.applied_rule_id} | set(decision.also_matched)
        for rule_id in decision.primitive_traces:
            assert rule_id in matched_ids


class TestNonAccessioningEvaluation:
    def test_first_match_by_priority_for_sample_prep(self, rule_index: RuleIndex) -> None:
        """Non-ACCESSIONING: first-match by ascending priority."""
        ctx = _make_ctx(
            state="SAMPLE_PREP_PROCESSING",
            event_type="processing_complete",
            event_data={"outcome": "success"},
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        # SP-001 fires on outcome=success at processing_complete; priority=1
        assert decision.applied_rule_id == "SP-001"
        assert decision.also_matched == ()

    def test_no_match_returns_dispatch_empty(self, rule_index: RuleIndex) -> None:
        """When no rule predicate matches, outcome=dispatch_empty.

        Uses an event type that no SP rule listens on so no predicate can fire.
        """
        ctx = _make_ctx(
            state="SAMPLE_PREP_PROCESSING",
            event_type="unrecognised_event_xyz",
            event_data={"outcome": "failure"},
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert decision.applied_rule_id is None
        assert decision.outcome == "dispatch_empty"


# ---------------------------------------------------------------------------
# rule_id_filter parameter
# ---------------------------------------------------------------------------


class TestRuleIdFilter:
    def test_no_filter_arg_same_as_existing_call(self, rule_index: RuleIndex) -> None:
        """evaluate(dispatch, ctx) == evaluate(dispatch, ctx, rule_id_filter=None)
        — the new kwarg is backward-compatible."""
        ctx = _make_ctx(
            patient_name=None,  # triggers ACC-001 (HOLD)
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision_no_kwarg = evaluate(dispatch, ctx)
        decision_none = evaluate(dispatch, ctx, rule_id_filter=None)
        assert decision_no_kwarg.applied_rule_id == decision_none.applied_rule_id
        assert decision_no_kwarg.outcome == decision_none.outcome
        assert decision_no_kwarg.dispatched_rule_ids == decision_none.dispatched_rule_ids

    def test_filter_none_returns_same_as_no_filter(self, rule_index: RuleIndex) -> None:
        """rule_id_filter=None produces identical dispatched_rule_ids."""
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        d1 = evaluate(dispatch, ctx)
        d2 = evaluate(dispatch, ctx, rule_id_filter=None)
        assert d1.dispatched_rule_ids == d2.dispatched_rule_ids

    def test_filter_to_acc001_fires_acc001(self, rule_index: RuleIndex) -> None:
        """rule_id_filter='ACC-001' on a dispatch containing ACC-001 fires only ACC-001."""
        ctx = _make_ctx(
            patient_name=None,  # triggers ACC-001 (HOLD)
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        # Verify ACC-001 is in the dispatch
        assert any(r.rule_id == "ACC-001" for r in dispatch.rules)

        decision = evaluate(dispatch, ctx, rule_id_filter="ACC-001")
        assert decision.applied_rule_id == "ACC-001"

    def test_filter_dispatched_rule_ids_is_full_original_list(self, rule_index: RuleIndex) -> None:
        """G9 contract: dispatched_rule_ids carries the FULL original list,
        not the filtered subset; also_matched is empty (the other matching
        rules were filtered out of iteration before they could match)."""
        ctx = _make_ctx(
            patient_name=None,  # triggers ACC-001 (HOLD)
            patient_sex=None,  # triggers ACC-002 (HOLD)
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        full_ids = tuple(r.rule_id for r in dispatch.rules)

        decision = evaluate(dispatch, ctx, rule_id_filter="ACC-001")
        assert decision.dispatched_rule_ids == full_ids
        # L-02 review: filter implies a single rule iterated;
        # other matches are not seen, so also_matched must be empty.
        assert decision.also_matched == ()

    def test_filter_to_nonexistent_rule_returns_dispatch_empty(self, rule_index: RuleIndex) -> None:
        """rule_id_filter='NONEXISTENT' → outcome='dispatch_empty';
        dispatched_rule_ids still carries the full original list."""
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        full_ids = tuple(r.rule_id for r in dispatch.rules)

        decision = evaluate(dispatch, ctx, rule_id_filter="NONEXISTENT")
        assert decision.outcome == "dispatch_empty"
        assert decision.applied_rule_id is None
        assert decision.dispatched_rule_ids == full_ids

    def test_filter_is_keyword_only(self, rule_index: RuleIndex) -> None:
        """rule_id_filter cannot be passed as a positional argument."""
        import inspect

        sig = inspect.signature(evaluate)
        param = sig.parameters["rule_id_filter"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    # M-03 review: first-match branch with rule_id_filter.
    # The previous tests all exercised ACCESSIONING (all_match); the
    # first-match branch (used by SAMPLE_PREP_PROCESSING and other
    # downstream states) needs its own filter coverage.
    def test_filter_in_first_match_mode_fires_filtered_rule(self, rule_index: RuleIndex) -> None:
        """First-match (non-ACCESSIONING) state with rule_id_filter
        resolves to the named rule and `dispatched_rule_ids` carries
        the full original list (G9)."""
        ctx = _make_ctx(
            state="SAMPLE_PREP_PROCESSING",
            event_type="processing_complete",
            event_data={"outcome": "success"},
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        full_ids = tuple(r.rule_id for r in dispatch.rules)
        # Sanity: SP-001 should be in the dispatch for this ctx.
        assert "SP-001" in full_ids

        decision = evaluate(dispatch, ctx, rule_id_filter="SP-001")
        assert decision.applied_rule_id == "SP-001"
        assert decision.dispatched_rule_ids == full_ids

    # M-04 review: filter resolves to a rule that IS in
    # dispatch but whose predicate evaluates false on this ctx. The
    # path differs from "rule_id absent from dispatch" — that one is
    # filtered out before iteration; this one is iterated but doesn't
    # match. Both produce dispatch_empty but via different code paths.
    def test_filter_to_rule_present_but_predicate_false_returns_dispatch_empty(
        self, rule_index: RuleIndex
    ) -> None:
        """Filter to a rule that is dispatched but doesn't fire on this ctx.

        ACC-001 fires only when patient_name is null. With patient_name set,
        ACC-001 is in dispatch (event_type matches) but its predicate is
        false. Filtering to ACC-001 should iterate exactly one rule, find
        no match, and return dispatch_empty with the full original
        dispatched_rule_ids preserved."""
        ctx = _make_ctx(patient_name="Jane Doe")  # ACC-001's predicate is false
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        full_ids = tuple(r.rule_id for r in dispatch.rules)
        assert "ACC-001" in full_ids  # ACC-001 IS in the dispatch

        decision = evaluate(dispatch, ctx, rule_id_filter="ACC-001")
        assert decision.outcome == "dispatch_empty"
        assert decision.applied_rule_id is None
        assert decision.dispatched_rule_ids == full_ids


# ---------------------------------------------------------------------------
# CanonicalizationTrace emission on was_unknown=True
# ---------------------------------------------------------------------------


class TestCanonicalizationTraceEmission:
    """Evaluator must emit CanonicalizationTrace for unknown field values."""

    def test_known_specimen_type_emits_no_trace(self, rule_index: RuleIndex) -> None:
        """Order with all-canonical values (already casefolded) produces no trace.

        Uses already-canonical values so the test fails iff canonicalization is
        broken, not merely iff case-normalization happens to catch an unknown value.
        """
        ctx = _make_ctx(
            specimen_type="biopsy",  # known value in pick list, already canonical
            anatomic_site="breast",
            fixative="formalin",
            ordered_tests=("her2",),  # already lowercase canonical form
            priority="routine",
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        canon_traces = [t for t in decision.decision_traces if t.kind == "canonicalization_warning"]
        assert canon_traces == [], (
            f"Expected no canonicalization traces for known values; got {canon_traces}"
        )

    def test_uppercase_known_value_emits_no_trace(self, rule_index: RuleIndex) -> None:
        """Order with a known value in non-canonical case produces no CanonicalizationTrace.

        Verifies that the case-insensitive match path works: 'HER2' casefolds to 'her2'
        which is in the pick list, so no trace fires.
        """
        ctx = _make_ctx(
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            ordered_tests=("HER2",),  # uppercase — casefolds to known pick-list entry
            priority="routine",
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        canon_traces = [t for t in decision.decision_traces if t.kind == "canonicalization_warning"]
        assert canon_traces == [], (
            f"Uppercase known value 'HER2' should not fire a trace; got {canon_traces}"
        )

    def test_unknown_specimen_type_emits_one_trace(self, rule_index: RuleIndex) -> None:
        """Order with an unknown specimen_type produces one CanonicalizationTrace."""
        ctx = _make_ctx(
            specimen_type="LCMI",  # unknown value — not in pick list
            anatomic_site="breast",
            fixative="formalin",
            ordered_tests=(),
            priority="routine",
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        canon_traces = [t for t in decision.decision_traces if t.kind == "canonicalization_warning"]
        assert len(canon_traces) == 1
        trace = canon_traces[0]
        assert trace.field == "specimen_type"
        assert trace.raw_value == "LCMI"
        assert trace.canonical_value_attempted == "lcmi"

    def test_two_unknown_fields_produce_two_traces(self, rule_index: RuleIndex) -> None:
        """Order with two unknown fields produces exactly two CanonicalizationTraces."""
        ctx = _make_ctx(
            specimen_type="LCMI",  # unknown
            anatomic_site="unknown_site_xyz",  # unknown
            fixative="formalin",
            ordered_tests=(),
            priority="routine",
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        canon_traces = [t for t in decision.decision_traces if t.kind == "canonicalization_warning"]
        fields_warned = {t.field for t in canon_traces}
        assert "specimen_type" in fields_warned
        assert "anatomic_site" in fields_warned
        assert len(canon_traces) == 2

    def test_ordered_tests_one_known_one_unknown_emits_one_trace(
        self, rule_index: RuleIndex
    ) -> None:
        """ordered_tests tuple with one known + one unknown produces one trace for unknown."""
        ctx = _make_ctx(
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            ordered_tests=("HER2", "UNKNOWN_TEST_XYZ"),  # HER2=known, UNKNOWN_TEST_XYZ=unknown
            priority="routine",
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        canon_traces = [t for t in decision.decision_traces if t.kind == "canonicalization_warning"]
        assert len(canon_traces) == 1
        trace = canon_traces[0]
        assert trace.field == "ordered_tests"
        assert trace.raw_value == "UNKNOWN_TEST_XYZ"
        assert trace.canonical_value_attempted == "unknown_test_xyz"

    def test_deduplication_same_unknown_value_same_field_one_trace(
        self, rule_index: RuleIndex
    ) -> None:
        """If the same unknown value appears multiple times in ordered_tests,
        only one CanonicalizationTrace is emitted (dedup by field+raw_value)."""
        ctx = _make_ctx(
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            ordered_tests=("UNKNOWN_TEST", "UNKNOWN_TEST"),  # duplicated unknown
            priority="routine",
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        canon_traces = [
            t
            for t in decision.decision_traces
            if t.kind == "canonicalization_warning" and t.field == "ordered_tests"
        ]
        assert len(canon_traces) == 1

    def test_multi_rule_dedup_one_trace_per_field(self, rule_index: RuleIndex) -> None:
        """Preflight runs once before rule iteration: even when multiple ACC rules
        match on the same unknown scalar field, exactly one CanonicalizationTrace
        fires for that field.

        patient_name=None triggers ACC-001; specimen_type='LCMI' is unknown.
        Multiple ACC rules may dispatch on the same order. The preflight scan
        runs once per evaluate() call, so only one trace fires for specimen_type
        regardless of how many rules see it.
        """
        ctx = _make_ctx(
            patient_name=None,  # triggers ACC-001 (missing patient name)
            specimen_type="LCMI",  # unknown specimen type
            anatomic_site="breast",
            fixative="formalin",
            ordered_tests=(),
            priority="routine",
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        specimen_traces = [
            t
            for t in decision.decision_traces
            if t.kind == "canonicalization_warning" and t.field == "specimen_type"
        ]
        assert len(specimen_traces) == 1, (
            f"Expected exactly one CanonicalizationTrace for specimen_type='LCMI'; "
            f"got {len(specimen_traces)}: {specimen_traces}"
        )


# ---------------------------------------------------------------------------
# order_id populated on all deterministic decision paths
# ---------------------------------------------------------------------------


class TestEvaluatorOrderIdPopulation:
    """evaluate() must populate decision.order_id == ctx.order.order_id on
    deterministic paths: first_match (match), first_match (no-match / dispatch_empty),
    and accessioning all_match.
    """

    def test_first_match_populates_order_id(self, rule_index: RuleIndex) -> None:
        """Non-ACCESSIONING first-match hit: decision.order_id == ctx.order.order_id."""
        ctx = _make_ctx(
            state="SAMPLE_PREP_PROCESSING",
            event_type="processing_complete",
            event_data={"outcome": "success"},
            order_id="O-SLICE2-FM",
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert decision.order_id == "O-SLICE2-FM"

    def test_no_match_dispatch_empty_populates_order_id(self, rule_index: RuleIndex) -> None:
        """Empty-dispatch early-return: decision.order_id == ctx.order.order_id.

        Exercises the ``if not rules:`` early-return path inside evaluate() where
        list_applicable_rules returns an empty DispatchResult (no rules are loaded
        for this state/event_type combination). This is NOT the tail _no_match_decision
        reached after iterating a non-empty rule set. See
        test_first_match_tail_no_match_populates_order_id for that path.
        """
        ctx = _make_ctx(
            state="MISSING_INFO_HOLD",
            event_type="some_event",
            order_id="O-SLICE2-NOMATCH",
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        assert dispatch.rules == (), "expected empty dispatch for this state/event combo"
        decision = evaluate(dispatch, ctx)
        assert decision.outcome == "dispatch_empty"
        assert decision.order_id == "O-SLICE2-NOMATCH"

    def test_first_match_tail_no_match_populates_order_id(self, rule_index: RuleIndex) -> None:
        """First-match tail _no_match_decision: decision.order_id == ctx.order.order_id.

        Exercises the ``return _no_match_decision(...)`` at the END of
        _evaluate_first_match after iterating a non-empty dispatched rule set whose
        predicates all evaluate False for this context. This is distinct from the
        empty-dispatch early-return in evaluate(). Three SP rules are dispatched for
        processing_complete but none match outcome='cancelled'.
        """
        ctx = _make_ctx(
            state="SAMPLE_PREP_PROCESSING",
            event_type="processing_complete",
            event_data={"outcome": "cancelled"},
            order_id="O-SLICE2-TAILNOMATCH",
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        assert dispatch.rules != (), "expected non-empty dispatch for this state/event combo"
        decision = evaluate(dispatch, ctx)
        assert decision.outcome == "dispatch_empty"
        assert decision.order_id == "O-SLICE2-TAILNOMATCH"

    def test_accessioning_all_match_populates_order_id(self, rule_index: RuleIndex) -> None:
        """ACCESSIONING all_match path: decision.order_id == ctx.order.order_id."""
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
            order_id="O-SLICE2-ACC",
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx)
        assert decision.applied_rule_id == "ACC-008"
        assert decision.order_id == "O-SLICE2-ACC"
