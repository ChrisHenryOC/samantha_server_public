"""Tests for dispatcher.list_applicable_rules and DispatchResult."""

import pytest

from samantha_server.engine.dispatcher import DispatchResult, list_applicable_rules
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
        ordered_tests=("HER2",),
        priority="ROUTINE",
        billing_info_present=True,
    )
    defaults.update(overrides)
    return Order(**defaults)  # type: ignore[arg-type]


def _make_ctx(state: str, event_type: str, event_data: dict | None = None) -> SpecimenContext:
    return SpecimenContext(
        order=_make_order(),
        current_state=state,
        flags=frozenset(),
        event=Event(
            event_type=event_type,
            event_data=event_data or {},
            step_index=0,
        ),
    )


class TestDispatchResult:
    def test_dispatch_result_has_rules_and_token(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx("ACCESSIONING", "order_received")
        result = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        assert isinstance(result, DispatchResult)
        assert isinstance(result.rules, tuple)
        assert isinstance(result.token, bytes)
        # Token is 64 bytes: 32-byte HMAC-SHA256 || 32-byte nonce
        assert len(result.token) == 64

    def test_token_is_random_per_call(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx("ACCESSIONING", "order_received")
        r1 = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        r2 = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        assert r1.token != r2.token


class TestDispatchByStep:
    def test_accessioning_state_returns_acc_rules(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx("ACCESSIONING", "order_received")
        result = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        rule_ids = {r.rule_id for r in result.rules}
        assert any(rid.startswith("ACC-") for rid in rule_ids)

    def test_sample_prep_qc_plus_sample_prep_qc_event_returns_sp004(
        self, rule_index: RuleIndex
    ) -> None:
        """SP-004/005/006 have event_type=sample_prep_qc; SP-001 does not.
        At SAMPLE_PREP_QC + sample_prep_qc, SP-004/005/006 should be dispatched."""
        ctx = _make_ctx("SAMPLE_PREP_QC", "sample_prep_qc")
        result = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        rule_ids = [r.rule_id for r in result.rules]
        assert "SP-004" in rule_ids
        assert "SP-001" not in rule_ids

    def test_sample_prep_processing_plus_processing_complete_includes_sp001(
        self, rule_index: RuleIndex
    ) -> None:
        """SP-001 listens on processing_complete."""
        ctx = _make_ctx("SAMPLE_PREP_PROCESSING", "processing_complete")
        result = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        rule_ids = [r.rule_id for r in result.rules]
        assert "SP-001" in rule_ids
        assert "SP-004" not in rule_ids


class TestDispatchByAppliesAt:
    def test_ihc_staining_dispatches_ihc001(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx("IHC_STAINING", "ihc_staining_complete")
        result = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        rule_ids = {r.rule_id for r in result.rules}
        assert "IHC-001" in rule_ids

    def test_ihc_qc_dispatches_ihc002(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx("IHC_QC", "ihc_qc")
        result = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        rule_ids = {r.rule_id for r in result.rules}
        assert "IHC-002" in rule_ids

    def test_ihc_staining_does_not_dispatch_ihc002(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx("IHC_STAINING", "ihc_staining_complete")
        result = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        rule_ids = {r.rule_id for r in result.rules}
        assert "IHC-002" not in rule_ids

    def test_ihc_qc_does_not_dispatch_ihc001(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx("IHC_QC", "ihc_qc")
        result = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        rule_ids = {r.rule_id for r in result.rules}
        assert "IHC-001" not in rule_ids


class TestDispatchEmptyState:
    def test_pass_through_state_returns_empty(self, rule_index: RuleIndex) -> None:
        """MISSING_INFO_HOLD has no rule catalog step and no applies_at — returns empty."""
        ctx = _make_ctx("MISSING_INFO_HOLD", "some_event")
        result = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        assert result.rules == ()

    def test_terminal_state_returns_empty(self, rule_index: RuleIndex) -> None:
        ctx = _make_ctx("ORDER_COMPLETE", "order_closed")
        result = list_applicable_rules(ctx, rule_index, session_id="test-session", ttl_sec=60)
        assert result.rules == ()
