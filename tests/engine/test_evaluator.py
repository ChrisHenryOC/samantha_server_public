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
        # The message must name the session mismatch, not claim
        # an HMAC failure — an operator chasing a stale-session caller should
        # not be misdirected toward a token-forgery investigation.
        with pytest.raises(UndispatchedRuleError, match="session mismatch"):
            evaluate(dispatch, ctx, session_id="session-B")

    def test_session_id_is_required_raises_type_error(self, rule_index: RuleIndex) -> None:
        """evaluate() without session_id raises TypeError.

        The session_id parameter is keyword-only and required; omitting it
        must raise TypeError so the cross-session replay guard can never
        be silently bypassed by a caller that forgets the kwarg.
        """
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        with pytest.raises(TypeError):
            evaluate(dispatch, ctx)  # type: ignore[call-arg]

    def test_session_id_is_keyword_only(self) -> None:
        """session_id cannot be passed as a positional argument.

        Mirrors test_filter_is_keyword_only: pins the keyword-only property
        so a future signature edit can't silently allow positional misuse.
        """
        import inspect

        sig = inspect.signature(evaluate)
        param = sig.parameters["session_id"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty

    def test_same_session_does_not_raise(self, rule_index: RuleIndex) -> None:
        """evaluate() with the matching session_id succeeds."""
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        # Should not raise — session matches.
        result = evaluate(dispatch, ctx, session_id="test-session")
        assert isinstance(result, EngineDecision)

    def test_expired_dispatch_raises_through_evaluate(self, rule_index: RuleIndex) -> None:
        """An expired dispatch token raises UndispatchedRuleError via evaluate().

        the TTL guard was only unit-tested at the dispatcher
        level; this pins the integration path. ttl_sec=-1 mints an
        already-expired token, so with a matching session the failure routes
        to the HMAC/expiry raise site.
        """
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=-1)
        with pytest.raises(UndispatchedRuleError, match="expired"):
            evaluate(dispatch, ctx, session_id="test-session")

    def test_wrong_token_raises(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        forged = DispatchResult(
            rules=dispatch.rules,
            token=bytes(b ^ 0xFF for b in dispatch.token),
            session_id="test-session",
        )
        # Matching session, forged token: the message must point at the HMAC,
        # not the session (discriminated raise sites).
        with pytest.raises(UndispatchedRuleError, match="HMAC"):
            evaluate(forged, ctx, session_id="test-session")

    def test_wrong_rules_raises(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        # Pin the ACCESSIONING multi-rule count so the `dispatch.rules[1:]`
        # slice below can never silently degenerate to the empty-tuple branch.
        # ACCESSIONING produces 12 rules; assert this assumption explicitly.
        assert len(dispatch.rules) > 1, (
            f"fixture must produce a multi-rule dispatch for ACCESSIONING; "
            f"got {len(dispatch.rules)} rules — rule-set regression?"
        )
        # Pass a tampered DispatchResult with one rule removed but the original token.
        # session_id matches the caller's so the session guard
        # passes and the test reaches the HMAC byte check it documents.
        tampered_rules = dispatch.rules[1:]
        tampered = DispatchResult(
            rules=tampered_rules, token=dispatch.token, session_id="test-session"
        )
        with pytest.raises(UndispatchedRuleError, match="HMAC"):
            evaluate(tampered, ctx, session_id="test-session")

    def test_wrong_rules_non_empty_to_non_empty_raises(self, rule_index: RuleIndex) -> None:
        """HMAC tamper is detected when rules are replaced with a different
        non-empty rule set, not just when they are reduced to the empty tuple.

        Grafts the SP-step rule set (non-empty) onto an ACCESSIONING dispatch token.
        Both the original and the grafted set are non-empty, so this exercises the
        HMAC binding beyond the degenerate empty-tuple case.

        Note: with matching session ids on both sides this is
        a pure HMAC rule-byte binding regression — it does not discriminate the
        session bypass (that coverage lives in the cross-session and
        TypeError contract tests above).
        """
        ctx_acc = _make_ctx()
        dispatch_acc = list_applicable_rules(
            ctx_acc, rule_index, session_id="test-session", ttl_sec=60
        )
        # Obtain a legitimately-dispatched non-empty rule set from a different step.
        ctx_sp = _make_ctx(
            state="SAMPLE_PREP_PROCESSING",
            event_type="processing_complete",
            event_data={"outcome": "success"},
        )
        dispatch_sp = list_applicable_rules(
            ctx_sp, rule_index, session_id="test-session", ttl_sec=60
        )
        # Verify both sets are non-empty and genuinely different.
        assert len(dispatch_acc.rules) > 0
        assert len(dispatch_sp.rules) > 0
        assert set(r.rule_id for r in dispatch_acc.rules) != set(
            r.rule_id for r in dispatch_sp.rules
        )
        # Graft the SP rules onto the ACC token — HMAC now covers different rule bytes.
        tampered = DispatchResult(
            rules=dispatch_sp.rules,
            token=dispatch_acc.token,
            session_id=dispatch_acc.session_id,
            expires_at=dispatch_acc.expires_at,
        )
        with pytest.raises(UndispatchedRuleError):
            evaluate(tampered, ctx_acc, session_id="test-session")

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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
        assert decision.applied_rule_id == "ACC-001"
        assert "ACC-002" in decision.also_matched

    def test_dispatched_rule_ids_matches_dispatch_result(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx, session_id="test-session")
        assert set(decision.dispatched_rule_ids) == {r.rule_id for r in dispatch.rules}

    def test_latency_us_is_positive_integer(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx, session_id="test-session")
        assert isinstance(decision.latency_us, int)
        assert decision.latency_us >= 0

    def test_event_input_hash_is_deterministic(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx()
        dispatch1 = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        dispatch2 = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        d1 = evaluate(dispatch1, ctx, session_id="test-session")
        d2 = evaluate(dispatch2, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
        assert decision.applied_rule_id is None
        assert decision.outcome == "dispatch_empty"


# ---------------------------------------------------------------------------
# rule_id_filter parameter
# ---------------------------------------------------------------------------


class TestRuleIdFilter:
    def test_no_filter_arg_same_as_existing_call(self, rule_index: RuleIndex) -> None:
        """Omitting rule_id_filter behaves the same as rule_id_filter=None.

        (session_id is always required; only the filter kwarg
        is optional here.)
        """
        ctx = _make_ctx(
            patient_name=None,  # triggers ACC-001 (HOLD)
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision_no_kwarg = evaluate(dispatch, ctx, session_id="test-session")
        decision_none = evaluate(dispatch, ctx, session_id="test-session", rule_id_filter=None)
        assert decision_no_kwarg.applied_rule_id == decision_none.applied_rule_id
        assert decision_no_kwarg.outcome == decision_none.outcome
        assert decision_no_kwarg.dispatched_rule_ids == decision_none.dispatched_rule_ids

    def test_filter_none_returns_same_as_no_filter(self, rule_index: RuleIndex) -> None:
        """rule_id_filter=None produces identical dispatched_rule_ids."""
        ctx = _make_ctx()
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        d1 = evaluate(dispatch, ctx, session_id="test-session")
        d2 = evaluate(dispatch, ctx, session_id="test-session", rule_id_filter=None)
        assert d1.dispatched_rule_ids == d2.dispatched_rule_ids

    def test_filter_to_acc001_fires_acc001(self, rule_index: RuleIndex) -> None:
        """rule_id_filter='ACC-001' on a dispatch containing ACC-001 fires only ACC-001."""
        ctx = _make_ctx(
            patient_name=None,  # triggers ACC-001 (HOLD)
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        # Verify ACC-001 is in the dispatch
        assert any(r.rule_id == "ACC-001" for r in dispatch.rules)

        decision = evaluate(dispatch, ctx, session_id="test-session", rule_id_filter="ACC-001")
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

        decision = evaluate(dispatch, ctx, session_id="test-session", rule_id_filter="ACC-001")
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

        decision = evaluate(dispatch, ctx, session_id="test-session", rule_id_filter="NONEXISTENT")
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

        decision = evaluate(dispatch, ctx, session_id="test-session", rule_id_filter="SP-001")
        assert decision.applied_rule_id == "SP-001"
        assert decision.dispatched_rule_ids == full_ids


# ---------------------------------------------------------------------------
# REVIEW_HOLD tier and flag-union semantics
# ---------------------------------------------------------------------------


class TestReviewHoldTier:
    """REVIEW_HOLD severity tier beats PROCEED; flag-union on dual-match."""

    def test_review_hold_beats_proceed_on_dual_match(self, rule_index: RuleIndex) -> None:
        """ACC-010 (REVIEW_HOLD: unknown specimen_type) beats ACC-007 (PROCEED: missing billing).

        Before the fix both were PROCEED and ACC-007 won alphabetically, silently
        dropping the clinically required PENDING_LLM_REVIEW transition.
        """
        ctx = _make_ctx(
            patient_name="Jane Doe",
            patient_sex="F",
            age=45,
            specimen_type="unknown_exotic_type",  # triggers ACC-010 (REVIEW_HOLD)
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=8.0,
            ordered_tests=("HER2",),
            billing_info_present=False,  # triggers ACC-007 (PROCEED)
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx, session_id="test-session")
        assert decision.applied_rule_id == "ACC-010"
        assert decision.next_state == "PENDING_LLM_REVIEW"
        assert "ACC-007" in decision.also_matched

    def test_review_hold_beats_proceed_anatomic_site_variant(self, rule_index: RuleIndex) -> None:
        """ACC-011 (REVIEW_HOLD: unknown anatomic_site) beats ACC-007 (PROCEED: missing billing)."""
        ctx = _make_ctx(
            patient_name="Jane Doe",
            patient_sex="F",
            age=45,
            specimen_type="biopsy",
            anatomic_site="unknown_exotic_site",  # triggers ACC-011 (REVIEW_HOLD)
            fixative="formalin",
            fixation_time_hours=8.0,
            ordered_tests=("HER2",),
            billing_info_present=False,  # triggers ACC-007 (PROCEED)
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx, session_id="test-session")
        assert decision.applied_rule_id == "ACC-011"
        assert decision.next_state == "PENDING_LLM_REVIEW"
        assert "ACC-007" in decision.also_matched

    def test_flag_union_review_hold_winner_includes_proceed_flags(
        self, rule_index: RuleIndex
    ) -> None:
        """When winner is REVIEW_HOLD, flags_added unions set_flags from PROCEED rules.

        ACC-010 sets LLM_REVIEW_REQUESTED; ACC-007 sets MISSING_INFO_PROCEED.
        Both must appear in flags_added so the post-review billing path works.
        """
        ctx = _make_ctx(
            patient_name="Jane Doe",
            patient_sex="F",
            age=45,
            specimen_type="unknown_exotic_type",  # triggers ACC-010 (REVIEW_HOLD)
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=8.0,
            ordered_tests=("HER2",),
            billing_info_present=False,  # triggers ACC-007 (PROCEED)
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx, session_id="test-session")
        # Exact tuple: winner's declared flags first, then rescued PROCEED-tier
        # flags in match order (H-2 — pins the concat direction).
        assert decision.flags_added == ("LLM_REVIEW_REQUESTED", "MISSING_INFO_PROCEED")

    def test_flag_union_no_duplicates(self, rule_index: RuleIndex) -> None:
        """flags_added must not contain duplicate flag names after union."""
        ctx = _make_ctx(
            patient_name="Jane Doe",
            patient_sex="F",
            age=45,
            specimen_type="unknown_exotic_type",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=8.0,
            ordered_tests=("HER2",),
            billing_info_present=False,
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx, session_id="test-session")
        assert len(decision.flags_added) == len(set(decision.flags_added))

    def test_flag_union_not_fired_for_hold_tier_winner(self, rule_index: RuleIndex) -> None:
        """HOLD-tier winner (SC-104-shaped ctx) must NOT union PROCEED flags.

        SC-104: ACC-001 (HOLD: missing patient_name) wins over ACC-007 (PROCEED: missing billing).
        ACC-007's MISSING_INFO_PROCEED must NOT appear in flags_added.
        """
        ctx = _make_ctx(
            patient_name=None,  # triggers ACC-001 (HOLD)
            patient_sex="F",
            age=45,
            specimen_type="biopsy",
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=8.0,
            ordered_tests=("HER2",),
            billing_info_present=False,  # triggers ACC-007 (PROCEED)
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx, session_id="test-session")
        assert decision.applied_rule_id == "ACC-001"
        assert "MISSING_INFO_PROCEED" not in decision.flags_added

    def test_flag_union_not_fired_for_reject_tier_winner(self, rule_index: RuleIndex) -> None:
        """Negative: REJECT winner must NOT union demoted flags.

        ACC-006 (REJECT: HER2 fixation out of [6, 72]h tolerance) wins over
        ACC-010 (REVIEW_HOLD: unknown specimen_type) and ACC-007 (PROCEED:
        missing billing). A DO_NOT_PROCESS specimen must not gain
        LLM_REVIEW_REQUESTED or MISSING_INFO_PROCEED.
        """
        ctx = _make_ctx(
            patient_name="Jane Doe",
            patient_sex="F",
            age=45,
            specimen_type="unknown_exotic_type",  # triggers ACC-010 (REVIEW_HOLD)
            anatomic_site="breast",
            fixative="formalin",
            fixation_time_hours=100.0,  # triggers ACC-006 (REJECT)
            ordered_tests=("HER2",),
            billing_info_present=False,  # triggers ACC-007 (PROCEED)
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx, session_id="test-session")
        assert decision.applied_rule_id == "ACC-006"
        assert "LLM_REVIEW_REQUESTED" not in decision.flags_added
        assert "MISSING_INFO_PROCEED" not in decision.flags_added

    def test_flag_union_triple_match_review_hold_sibling_contributes_nothing(
        self, rule_index: RuleIndex
    ) -> None:
        """triple-match (M): ACC-010 + ACC-011 + ACC-007 co-fire.

        Unknown specimen AND unknown site AND missing billing: ACC-010 wins the
        intra-REVIEW_HOLD tie alphabetically; ACC-011 (REVIEW_HOLD sibling) is
        demoted but contributes no flags via the union (PROCEED-tier only);
        ACC-007's MISSING_INFO_PROCEED is rescued. Flags are identical
        regardless of which REVIEW_HOLD rule wins the tie.
        """
        ctx = _make_ctx(
            patient_name="Jane Doe",
            patient_sex="F",
            age=45,
            specimen_type="unknown_exotic_type",  # triggers ACC-010 (REVIEW_HOLD)
            anatomic_site="unknown_exotic_site",  # triggers ACC-011 (REVIEW_HOLD)
            fixative="formalin",
            fixation_time_hours=8.0,
            ordered_tests=("HER2",),
            billing_info_present=False,  # triggers ACC-007 (PROCEED)
        )
        dispatch = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        decision = evaluate(dispatch, ctx, session_id="test-session")
        assert decision.applied_rule_id == "ACC-010"
        assert set(decision.also_matched) >= {"ACC-011", "ACC-007"}
        assert decision.flags_added == ("LLM_REVIEW_REQUESTED", "MISSING_INFO_PROCEED")

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

        decision = evaluate(dispatch, ctx, session_id="test-session", rule_id_filter="ACC-001")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
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
        decision = evaluate(dispatch, ctx, session_id="test-session")
        assert decision.applied_rule_id == "ACC-008"
        assert decision.order_id == "O-SLICE2-ACC"


# ---------------------------------------------------------------------------
# CANONICAL_FIELDS_SORTED and event_input_hash consistency
# (review fix #4)
# ---------------------------------------------------------------------------


def test_canonical_fields_sorted_constant_equals_sorted_canonical_fields() -> None:
    """CANONICAL_FIELDS_SORTED equals tuple(sorted(CANONICAL_FIELDS)).

    Pins the content so the hoist cannot silently introduce a different ordering.
    """
    from samantha_server.canonicalization import CANONICAL_FIELDS
    from samantha_server.engine.evaluator import CANONICAL_FIELDS_SORTED

    assert tuple(sorted(CANONICAL_FIELDS)) == CANONICAL_FIELDS_SORTED, (
        "CANONICAL_FIELDS_SORTED must equal tuple(sorted(CANONICAL_FIELDS)) exactly"
    )


def test_canonical_fields_sorted_is_tuple() -> None:
    """CANONICAL_FIELDS_SORTED is a tuple (not a list or frozenset)."""
    from samantha_server.engine import evaluator

    assert isinstance(evaluator.CANONICAL_FIELDS_SORTED, tuple), (
        f"CANONICAL_FIELDS_SORTED must be a tuple; got {type(evaluator.CANONICAL_FIELDS_SORTED)}"
    )


def test_evaluate_event_input_hash_consistent_across_dispatch_paths() -> None:
    """event_input_hash is the same value regardless of which
    internal branch (no-match, accessioning, first-match) produces the decision.

    Exercises the accessioning branch with identical ctx and asserts the hash
    on the returned decision equals compute_event_input_hash(ctx). This pins the
    single-computation contract: the hash is computed once at the top of evaluate()
    and threaded through all internal helpers.
    """
    from samantha_server.engine.decision import compute_event_input_hash
    from samantha_server.engine.dispatcher import list_applicable_rules
    from samantha_server.engine.evaluator import evaluate
    from samantha_server.models.context import Event, Order, SpecimenContext

    order = Order(
        order_id="O-GH387",
        patient_name="Jane Doe",
        patient_sex="F",
        age=45,
        specimen_type="biopsy",
        anatomic_site="breast",
        fixative="formalin",
        fixation_time_hours=8.0,
        ordered_tests=("HER2",),
        priority="routine",
        billing_info_present=True,
    )
    ctx = SpecimenContext(
        order=order,
        current_state="ACCESSIONING",
        flags=frozenset(),
        event=Event(event_type="order_received", event_data={}, step_index=0),
    )

    specs = load_rule_specs(SPECS_DIR)
    index = RuleIndex(specs)
    dispatch = list_applicable_rules(ctx, index, session_id="test-gh387", ttl_sec=60)
    decision = evaluate(dispatch, ctx, session_id="test-gh387")

    expected_hash = compute_event_input_hash(ctx)
    assert decision.event_input_hash == expected_hash, (
        f"event_input_hash on decision ({decision.event_input_hash!r}) must "
        f"equal compute_event_input_hash(ctx) ({expected_hash!r})"
    )
